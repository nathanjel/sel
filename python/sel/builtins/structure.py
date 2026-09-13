from .. import decimal as D
from ..errors import fail
from ..registry import INF, define
from ..value import NONE, Value, iter_elements, iter_values, structural_hash


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
    value.force()
    if value.is_null() or (value.kind == NONE and value.size() == 0):
        return None
    if value.is_list and value.storage is not None and value.storage:
        return value.storage[0].force()
    entries = elements(value)
    return entries[0][1] if entries else value


define('COUNT', 1, 1, fn=lambda args, ctx: Value.int(args.val(0).size()))

define('INDEXES', 1, 1,
       fn=lambda args, ctx: Value.list([Value.text(k) for k in args.val(0).keys()]))

define('HAS', 2, 2, fn=lambda args, ctx: Value.bool(args.val(0).has(args.text(1))))

define('LIST', 0, INF,
       fn=lambda args, ctx: Value.list([args.val(i).clone() for i in range(args.count())]))


def _record(args, ctx):
    entries = []
    for i in range(0, args.count(), 2):
        entries.append((args.text(i), args.val(i + 1).clone()))
    return Value.from_entries(entries)


define('RECORD', 0, INF,
       arity_error=lambda count: f"RECORD takes an even number of arguments (key-value pairs), got {count}" if count % 2 != 0 else None,
       fn=_record)


def _lazy_record(args, ctx):
    entries = []
    for i in range(0, args.count(), 2):
        key = args.text(i)
        node = args.node(i + 1)
        if node.t in ('num', 'text', 'bool', 'null'):
            value = args.eval_node(node)
        else:
            captured = ctx.copy_current()
            value = Value.thunk(lambda node=node, captured=captured:
                                args.eval_node(node, captured))
        entries.append((key, value))
    return Value.from_entries(entries)


define('LAZY_RECORD', 0, INF, lazy=True, binds=True,
       arity_error=lambda count: f"LAZY_RECORD takes an even number of arguments (key-value pairs), got {count}" if count % 2 != 0 else None,
       fn=_lazy_record)


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
    rows = []
    for _, row in elements(value):
        entries = []
        for column in columns:
            if row.has(column):
                entries.append((column, row.get(column).clone()))
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
        bucket = buckets.setdefault(key, [])
        if not any(item.eql(existing) for existing in bucket):
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
        # Positive integer text is already canonical.  Avoid a Dec allocation
        # and formatting pass for the dominant integer-key join case; all other
        # numeric spellings still use the exact cached decimal path.
        if value.kind == 'TEXT':
            text = value.scalar
            if (text and text.isascii() and text.isdigit()
                    and (len(text) == 1 or text[0] != '0')):
                return text
        try:
            return D.format(value.as_decimal())
        except Exception:
            return None
    return value.scalar if value.kind == 'TEXT' else None


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
            cached = (keys, old_shape.size, add_lower)
            old_shape.alias_cache[table_name] = cached
        keys, old_size, add_lower = cached
        storage = row.storage[:old_size]
        storage.append(row)
        if add_lower:
            storage.append(row)
        return Value.shaped(keys, storage)
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
    entries = []
    present = set()

    def put(key, value):
        if key not in present:
            present.add(key)
            entries.append((key, value))

    for key, value in left.entries():
        if is_nested_record(value):
            put(key, value)

    low1 = b1.lower()
    put(b1, left)
    if low1 != b1:
        put(low1, left)
    if b1 != '_1':
        put('_1', left)

    actual_right = right if right is not None else null_right
    actual_right = actual_right if actual_right is not None else Value.none()
    low2 = b2.lower()
    put(b2, actual_right)
    if low2 != b2:
        put(low2, actual_right)
    if b2 != '_2':
        put('_2', actual_right)

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
    if sample is None or sample.shape is None:
        return lambda left, right: make_joined_row(
            left, right, b1, b2, promoted_left, promoted_right, table_left, null_right)

    output_shape = sample.shape
    left_shape = sample_left.shape
    right_shape = sample_right.shape
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

    def project(left, right):
        if right is None:
            return make_joined_row(left, None, b1, b2, promoted_left,
                                   promoted_right, table_left, null_right)
        # Rows with a different shape are legal (irregular input is supported),
        # but cannot use the precompiled offsets.  The fallback is cold; regular
        # rows take only integer slot reads and one destination allocation.
        if left.shape is not left_shape or right.shape is not right_shape:
            return make_joined_row(left, right, b1, b2, promoted_left,
                                   promoted_right, table_left, null_right)
        storage = [None] * len(actions)
        for index, (kind, key) in enumerate(actions):
            if kind == 'left':
                storage[index] = left
            elif kind == 'right':
                storage[index] = right
            elif kind == 'left-slot':
                storage[index] = left.storage[key]
            elif kind == 'right-slot':
                storage[index] = right.storage[key]
            elif kind == 'left-key':
                storage[index] = left.get(key) or Value.none()
            elif kind == 'right-key':
                storage[index] = right.get(key) or Value.none()
            else:
                storage[index] = Value.none()
        return Value._from_shape(output_shape, storage)

    return project


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
    sample_left = ensure_row_table_alias(first_left, b1) if first_left is not None else None
    sample_right = ensure_row_table_alias(first_right, b2) if first_right is not None else None
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
                row = ensure_row_table_alias(item, b2)
                frame_right[b2] = row
                frame_right[b2.lower()] = row
                frame_right['_2'] = row
                key = canonical_join_key(args.eval_node(right_expr), numeric)
                if key is not None:
                    buckets.setdefault(key, []).insert(0, row)
        finally:
            ctx.pop_frame()

        frame_left = {b1: None, b1.lower(): None, '_1': None, '_': None}
        ctx.push_frame(frame_left)
        try:
            for item in iter_collection_items(left_value):
                row = ensure_row_table_alias(item, b1)
                frame_left[b1] = row
                frame_left[b1.lower()] = row
                frame_left['_1'] = row
                frame_left['_'] = row
                key = canonical_join_key(args.eval_node(left_expr), numeric)
                matches = buckets.get(key) if key is not None else None
                if matches:
                    for right in reversed(matches):
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
                left = ensure_row_table_alias(left_item, b1)
                frame[b1] = left
                frame[b1.lower()] = left
                frame['_1'] = left
                frame['_'] = left
                matched = [False]

                for right_item in iter_collection_items(right_value):
                    right = ensure_row_table_alias(right_item, b2)
                    frame[b2] = right
                    frame[b2.lower()] = right
                    frame['_2'] = right
                    if args.eval_node(predicate).as_bool(predicate.pos):
                        matched[0] = True
                        output.append(project(left, right))

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
