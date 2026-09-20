from .. import decimal as D
from ..errors import fail
from ..registry import INF, define
from ..value import NONE, Value, iter_elements, iter_values, structural_hash, _record_shape


def elements(value):
    return list(iter_elements(value))


def iter_collection_items(value):
    """Yield collection values without building ``(key, value)`` tuples.

    ``elements`` is the public/key-preserving helper.  LINK's nested-loop and
    hash-join paths only need the values, so using it there would allocate a
    complete entry list for every probe (and again for every nested-loop left
    row).  This mirrors Lisp's FOR-EACH-COLLECTION-ITEM and keeps the common
    shaped/list path on the flat storage directly.
    """
    yield from iter_values(value)


def first_collection_item(value):
    if value.is_null() or (value.kind == NONE and value.size() == 0):
        return None
    if value.is_list and value.storage is not None and value.storage:
        return value.storage[0]
    entries = elements(value)
    return entries[0][1] if entries else value


define('COUNT', 1, 1, fn=lambda args, ctx: Value.int(args.val(0).size()))

define('INDEXES', 1, 1,
       fn=lambda args, ctx: Value.list([Value.text(k) for k in args.val(0).keys()]))

define('HAS', 2, 2, fn=lambda args, ctx: Value.bool(args.val(0).has(args.text(1))))

define('LIST', 0, INF,
       fn=lambda args, ctx: Value.list([args.val(i) for i in range(args.count())]))


def _record(args, ctx):
    count = args.count()
    if count == 0:
        return Value.none()
    keys = [args.text(i) for i in range(0, count, 2)]
    values = [args.val(i + 1) for i in range(0, count, 2)]
    return Value.record(keys, values)


define('RECORD', 0, INF,
       arity_error=lambda count: f"RECORD takes an even number of arguments (key-value pairs), got {count}" if count % 2 != 0 else None,
       fn=_record)


def _take(args, ctx):
    value = args.val(0)
    count = args.non_neg_int(1)
    if count == 0 or value.is_null():
        return Value.list([])
    if value.is_list and value.storage is not None:
        return Value.list(value.storage[:count])
    return Value.list([item for _, item in elements(value)[:count]])


define('TAKE', 2, 2, fn=_take)


def _drop(args, ctx):
    value = args.val(0)
    count = args.non_neg_int(1)
    if value.is_null():
        return Value.list([])
    if value.is_list and value.storage is not None:
        return Value.list(value.storage[count:])
    return Value.list([item for _, item in elements(value)[count:]])


define('DROP', 2, 2, fn=_drop)


def _select_cols(args, ctx):
    value = args.val(0)
    if value.is_null():
        return Value.list([])
    columns = [args.text(i) for i in range(1, args.count())]
    if (value.is_list and value.storage is not None
            and value.storage and value.storage[0].shape is not None):
        sample_shape = value.storage[0].shape
        slots = [sample_shape.key_map.get(col) for col in columns]
        if all(s is not None for s in slots):
            all_uniform = all(r.shape is sample_shape for r in value.storage)
            if all_uniform:
                out_shape = _record_shape(tuple(columns))
                return Value.list([Value._from_shape(out_shape, [r.storage[s] for s in slots])
                                   for r in value.storage])
    rows = []
    for _, row in elements(value):
        entries = []
        for column in columns:
            if row.has(column):
                entries.append((column, row.get(column)))
        rows.append(Value.from_entries(entries))
    return Value.list(rows)


define('SELECT_COLS', 2, INF, fn=_select_cols)


def _dedupe(args, ctx):
    value = args.val(0)
    if value.is_null():
        return Value.list([])
    buckets = {}
    out = []
    for _, item in elements(value):
        key = structural_hash(item)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = [item]
            out.append(item)
            continue
        found = False
        for existing in bucket:
            if item.eql(existing):
                found = True
                break
        if not found:
            bucket.append(item)
            out.append(item)
    return Value.list(out)


define('DISTINCT', 1, 1, fn=_dedupe)
define('DEDUPE', 1, 1, fn=_dedupe)


# --- relational links ------------------------------------------------------

def expr_depends_only_on(node, allowed):
    if node is None:
        return True
    if node.t == 'var':
        return node.name.upper() in allowed
    if node.t == 'index':
        return expr_depends_only_on(node.obj, allowed) and expr_depends_only_on(node.idx, allowed)
    if node.t == 'call':
        return all(expr_depends_only_on(item, allowed) for item in node.args)
    if node.t == 'bin':
        return expr_depends_only_on(node.l, allowed) and expr_depends_only_on(node.r, allowed)
    if node.t == 'un':
        return expr_depends_only_on(node.x, allowed)
    if node.t == 'assign':
        return expr_depends_only_on(node.target, allowed) and expr_depends_only_on(node.value, allowed)
    if node.t in ('seq', 'list'):
        return all(expr_depends_only_on(item, allowed) for item in node.items)
    return True


def try_extract_equi_keys(node, b1, b2):
    if node is None or node.t != 'bin' or node.op not in ('==', '$=='):
        return None
    left_names = {b1.upper(), b1.lower().upper(), '_1', '_'}
    right_names = {b2.upper(), b2.lower().upper(), '_2'}
    if expr_depends_only_on(node.l, left_names) and expr_depends_only_on(node.r, right_names):
        return node.l, node.r, node.op == '=='
    if expr_depends_only_on(node.r, left_names) and expr_depends_only_on(node.l, right_names):
        return node.r, node.l, node.op == '=='
    return None


def single_relation_name(node):
    if node is None:
        return None
    if node.t == 'var':
        return node.name
    if node.t == 'call' and node.args and node.name not in ('LINK', 'LINK_LEFT'):
        return single_relation_name(node.args[0])
    return None


def canonical_join_key(value, numeric):
    if value is None or value.is_null():
        return None
    if numeric:
        d = value._dec_val
        if d is None:
            if value.kind == 'TEXT' and value._scalar is not None:
                text = value._scalar
                if text.isascii() and (text.isdigit() or (text.startswith('-') and text[1:].isdigit())):
                    try:
                        return int(text)
                    except Exception:
                        pass
            try:
                d = value.as_decimal()
            except Exception:
                return None

        if d.scale == 0:
            return -d.digits if d.neg else d.digits
        if d.digits == 0:
            return 0
        digits = d.digits
        scale = d.scale
        while scale > 0 and digits % 10 == 0:
            digits //= 10
            scale -= 1
        if scale == 0:
            return -digits if d.neg else digits
        return (-digits if d.neg else digits, scale)

    return value._scalar if value._scalar is not None else (value.scalar if value.kind == 'TEXT' else None)


def ensure_row_table_alias(row, table_name):
    if not table_name or table_name == '_1' or row.has(table_name):
        return row
    lower = table_name.lower()
    if row.shape is not None:
        old_shape = row.shape
        cached = old_shape.alias_cache.get(table_name)
        if cached is None:
            add_lower = lower != table_name and lower not in old_shape.key_map
            keys = tuple(list(old_shape.keys) + [table_name] + ([lower] if add_lower else []))
            target_shape = _record_shape(keys)
            cached = (target_shape, old_shape.size, add_lower)
            old_shape.alias_cache[table_name] = cached
        target_shape, old_size, add_lower = cached
        storage = [*row.storage, row, row] if add_lower else [*row.storage, row]
        return Value._from_shape(target_shape, storage)
    entries = row.entries()
    entries.append((table_name, row))
    if lower != table_name and not row.has(lower):
        entries.append((lower, row))
    return Value.from_entries(entries)


def make_null_record(sample, table_name):
    entries = []
    if sample is not None:
        entries.extend((key, Value.none()) for key in sample.keys())
    if table_name:
        entries.append((table_name, Value.none()))
        lower = table_name.lower()
        if lower != table_name:
            entries.append((lower, Value.none()))
    return Value.from_entries(entries)


def is_nested_record(value):
    return value.size() > 0 and not value.is_list


def make_joined_row(left, right, b1, b2, promoted_left, promoted_right,
                    table_left, null_right):
    # Each key once, where it first occurred (spec §7.4): a carried or promoted
    # key keeps its first value, a binder key holds the row this LINK bound
    # even where an earlier LINK's `_1` or a relation joined twice carried a
    # record of the same name. That is the row the compiled projector below
    # builds from the shape (binders first, then slots); with a first-wins
    # `present` set for every key, this path kept the old value instead.
    entries = []
    slot = {}

    def put(key, value):
        if key in slot:
            return
        slot[key] = len(entries)
        entries.append((key, value))

    def bind(key, value):
        if key in slot:
            entries[slot[key]] = (key, value)
        else:
            put(key, value)

    for key, value in left.entries():
        if is_nested_record(value):
            put(key, value)

    low1 = b1.lower()
    bind(b1, left)
    if low1 != b1:
        bind(low1, left)
    if b1 != '_1':
        bind('_1', left)

    actual_right = right if right is not None else null_right
    actual_right = actual_right if actual_right is not None else Value.none()
    low2 = b2.lower()
    bind(b2, actual_right)
    if low2 != b2:
        bind(low2, actual_right)
    if b2 != '_2':
        bind('_2', actual_right)

    for key in promoted_left:
        value = left.get(key)
        if value is not None:
            put(key, value)
    if right is not None:
        for key in promoted_right:
            value = right.get(key)
            if value is not None and not value.is_null():
                put(key, value)
    return Value.from_entries(entries)


def make_join_projector(sample_left, sample_right, b1, b2,
                        promoted_left, promoted_right, table_left, null_right):
    sample = (make_joined_row(sample_left, sample_right, b1, b2,
                              promoted_left, promoted_right, table_left, null_right)
              if sample_left is not None and sample_right is not None else None)
    left_shape = sample_left.shape if sample_left is not None else None
    right_shape = sample_right.shape if sample_right is not None else None
    if sample is None or sample.shape is None or left_shape is None or right_shape is None:
        return lambda left, right: make_joined_row(
            left, right, b1, b2, promoted_left, promoted_right, table_left, null_right)

    output_shape = sample.shape
    left_aliases = {b1, b1.lower(), '_1'}
    right_aliases = {b2, b2.lower(), '_2'}
    actions = []
    for key in sample.shape.keys:
        if key in left_aliases:
            actions.append(('left', None))
        elif key in right_aliases:
            actions.append(('right', None))
        elif key in table_left or key in promoted_left:
            slot = left_shape.key_map.get(key) if left_shape is not None else None
            actions.append(('left-slot', slot) if slot is not None else ('left-key', key))
        elif key in promoted_right:
            slot = right_shape.key_map.get(key) if right_shape is not None else None
            actions.append(('right-slot', slot) if slot is not None else ('right-key', key))
        else:
            actions.append(('none', None))

    elements = []
    none_val = Value.none()
    for kind, key in actions:
        if kind == 'left':
            elements.append('left')
        elif kind == 'right':
            elements.append('right')
        elif kind == 'left-slot':
            elements.append(f'l_s[{key}]')
        elif kind == 'right-slot':
            elements.append(f'r_s[{key}]')
        elif kind == 'left-key':
            elements.append(f'(left.get({key!r}) or _none)')
        elif kind == 'right-key':
            elements.append(f'(right.get({key!r}) or _none)')
        else:
            elements.append('_none')

    code = f"""def _compiled_project(left_shape, right_shape, output_shape, fallback, _from_shape, _none):
    def project(left, right):
        if right is None or left.shape is not left_shape or right.shape is not right_shape:
            return fallback(left, right)
        l_s = left.storage
        r_s = right.storage
        return _from_shape(output_shape, [{", ".join(elements)}])
    return project
"""
    local_ns = {}
    exec(code, {}, local_ns)
    fallback = lambda l, r: make_joined_row(l, r, b1, b2, promoted_left, promoted_right, table_left, null_right)
    return local_ns['_compiled_project'](left_shape, right_shape, output_shape, fallback, Value._from_shape, none_val)


def _link(args, ctx, left_join):
    count = args.count()
    if count not in (3, 5):
        fail('E_ARITY', f'{args.name} takes 3 or 5 arguments, got {count}', args.pos)
    left_value = args.val(0)
    right_value = args.val(1)
    b1, b2 = '_1', '_2'
    if count == 3:
        b1 = single_relation_name(args.node(0)) or b1
        b2 = single_relation_name(args.node(1)) or b2
        predicate = args.node(2)
    else:
        b1 = args.symbol(2)
        b2 = args.symbol(3)
        predicate = args.node(4)
    if left_value.is_null():
        return Value.list([])

    first_left = first_collection_item(left_value)
    first_right = first_collection_item(right_value)
    if first_left is None or first_right is None:
        if not left_join or first_left is None:
            return Value.list([])
    needs_left_alias = bool(b1 and b1 != '_1' and (first_left is None or not first_left.has(b1)))
    sample_left = ensure_row_table_alias(first_left, b1) if (first_left is not None and needs_left_alias) else first_left
    sample_right = first_right
    null_right = make_null_record(sample_right, b2) if left_join else None
    left_keys = sample_left.keys() if sample_left is not None else []
    right_keys = sample_right.keys() if sample_right is not None else []
    right_key_set = {key.upper() for key in right_keys}
    left_key_set = {key.upper() for key in left_keys}
    promoted_left = [key for key in left_keys
                     if not is_nested_record(sample_left.get(key))
                     and key.upper() not in right_key_set] if sample_left else []
    promoted_right = [key for key in right_keys
                      if not is_nested_record(sample_right.get(key))
                      and key.upper() not in left_key_set] if sample_right else []
    table_left = [key for key in left_keys if is_nested_record(sample_left.get(key))] if sample_left else []
    project = make_join_projector(sample_left, sample_right, b1, b2,
                                  promoted_left, promoted_right, table_left, null_right)
    equi = try_extract_equi_keys(predicate, b1, b2)
    output = []

    if equi is not None and sample_right is not None:
        left_expr, right_expr, numeric = equi
        buckets = {}
        frame_right = {b2: None, b2.lower(): None, '_2': None}
        ctx.push_frame(frame_right)
        try:
            for item in iter_collection_items(right_value):
                frame_right[b2] = item
                frame_right[b2.lower()] = item
                frame_right['_2'] = item
                key = canonical_join_key(args.eval_node(right_expr), numeric)
                if key is not None:
                    buckets.setdefault(key, []).append(item)
        finally:
            ctx.pop_frame()

        frame_left = {b1: None, b1.lower(): None, '_1': None, '_': None}
        ctx.push_frame(frame_left)
        try:
            for item in iter_collection_items(left_value):
                row = ensure_row_table_alias(item, b1) if needs_left_alias else item
                frame_left[b1] = row
                frame_left[b1.lower()] = row
                frame_left['_1'] = row
                frame_left['_'] = row
                key = canonical_join_key(args.eval_node(left_expr), numeric)
                matches = buckets.get(key) if key is not None else None
                if matches:
                    for right in matches:
                        output.append(project(row, right))
                elif left_join:
                    output.append(project(row, None))
        finally:
            ctx.pop_frame()
    else:
        frame = {b1: None, b1.lower(): None, '_1': None, '_': None,
                 b2: None, b2.lower(): None, '_2': None}
        ctx.push_frame(frame)
        try:
            for left_item in iter_collection_items(left_value):
                left = ensure_row_table_alias(left_item, b1) if needs_left_alias else left_item
                frame[b1] = left
                frame[b1.lower()] = left
                frame['_1'] = left
                frame['_'] = left
                matched = [False]

                for right_item in iter_collection_items(right_value):
                    frame[b2] = right_item
                    frame[b2.lower()] = right_item
                    frame['_2'] = right_item
                    if args.eval_node(predicate).as_bool(predicate.pos):
                        matched[0] = True
                        output.append(project(left, right_item))

                if left_join and not matched[0]:
                    output.append(project(left, None))
        finally:
            ctx.pop_frame()
    return Value.list(output)


define('LINK', 3, 5, lazy=True, binds=True,
       arity_error=lambda count: None if count in (3, 5) else f'LINK takes 3 or 5 arguments, got {count}',
       fn=lambda args, ctx: _link(args, ctx, False))
define('LINK_LEFT', 3, 5, lazy=True, binds=True,
       arity_error=lambda count: None if count in (3, 5) else f'LINK_LEFT takes 3 or 5 arguments, got {count}',
       fn=lambda args, ctx: _link(args, ctx, True))
