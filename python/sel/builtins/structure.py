"""The structural builtins: COUNT, INDEXES, HAS, LIST, RECORD, TAKE, DROP,
SELECT_COLS, DISTINCT and DEDUPE, and the relational LINK and LINK_LEFT -- their
equi-join and nested-loop paths, and the run-time pre-filter that applies a
FILTER's conjuncts inside a join (spec §7.4)."""

from .._budget import check_collection
from ..errors import SelError, fail
from ..lexer import ascii_upper
from ..registry import INF, define
from .aggregate import _SCALAR_FRESH_CALLS
from ..parser import Node
from ..value import NONE, TEXT, Value, elements, iter_values, structural_hash, _record_shape


def _upper_name(s):
    """Names compare ASCII-case-insensitively (spec §2, §7.4): only a-z move.
    str.upper() folds "ß" to "SS" and "ſ" to "S", which made distinct field
    names collide in joined rows. The builtin is kept for the all-ASCII names
    that are nearly every name."""
    return s.upper() if s.isascii() else ascii_upper(s)


def first_collection_item(value):
    if value.kind == NONE and value.size() == 0:
        return None
    if value.is_list and value.storage is not None and value.storage:
        return value.storage[0]
    # The first element only: building every entry of a dict-mode record to read the
    # first one is what `elements()` did (PY-P26). iter_values yields the same first
    # item, and nothing at all exactly where elements() was empty.
    for item in iter_values(value):
        return item
    return value


define('COUNT', 1, 1, fn=lambda args, ctx: Value.int(args.val(0).size()))

define('INDEXES', 1, 1,
       fn=lambda args, ctx: Value._list_owned([Value.text(k) for k in args.val(0).keys()]))

define('HAS', 2, 2, fn=lambda args, ctx: Value.bool(args.val(0).has(args.text(1))))

# LIST and RECORD collect like `,` does, so they copy what they collect (SPEC 3.4)
# and the copy is one level down, where a value past the cap is refused at this
# call rather than at 0:0 by whatever walks it later.
def _list(args, ctx):
    check_collection(args.count(), args.pos)
    return Value._list_owned([args.val(i).clone(args.pos, 2) for i in range(args.count())])


define('LIST', 0, INF, fn=_list)


_COALESCE_OPS = ('??', '???')


def _record(args, ctx):
    count = args.count()
    if count == 0:
        return Value.none()
    check_collection(count // 2, args.pos)
    shape = args.record_shape
    pos = args.pos
    # Without a precomputed shape the keys are evaluated first, as they always
    # were, and then the values.
    keys = None if shape is not None else [args.text(i) for i in range(0, count, 2)]
    # Each field value is copied one level down unless its node computes a scalar
    # of its own (aggregate.arg_needs_copy, spelled out here: this loop runs once
    # per row of a MAP(RECORD(...)), and two helper calls per field were the
    # measurable part of it).
    nodes = args.nodes
    vals = []
    for i in range(1, count, 2):
        v = args.val(i)
        n = nodes[i]
        t = n.t
        if t == 'un' or (t == 'bin' and n.op not in _COALESCE_OPS) or (
                t == 'call' and n.name in _SCALAR_FRESH_CALLS):
            vals.append(v)
        else:
            vals.append(v.clone(pos, 2))
    if shape is not None:
        return Value._from_shape(shape, vals)
    return Value._record_owned(keys, vals)


define('RECORD', 0, INF,
       fn=_record)


def _take(args, ctx):
    value = args.val(0)
    count = args.non_neg_int(1)
    if count == 0 or value.is_null():
        return Value._list_owned([])
    if value.is_list and value.storage is not None:
        return Value._list_owned(value.storage[:count])
    return Value._list_owned([item for _, item in elements(value)[:count]])


define('TAKE', 2, 2, fn=_take)


def _drop(args, ctx):
    value = args.val(0)
    count = args.non_neg_int(1)
    if value.is_null():
        return Value._list_owned([])
    if value.is_list and value.storage is not None:
        return Value._list_owned(value.storage[count:])
    return Value._list_owned([item for _, item in elements(value)[count:]])


define('DROP', 2, 2, fn=_drop)


def _select_cols(args, ctx):
    value = args.val(0)
    if value.is_null():
        return Value._list_owned([])
    # A record has one field per name: a column named twice is kept once, at its
    # first place (the fast path below built duplicate keys).
    columns = list(dict.fromkeys(args.text(i) for i in range(1, args.count())))
    if (value.is_list and value.storage is not None
            and value.storage and value.storage[0].shape is not None):
        sample_shape = value.storage[0].shape
        slots = [sample_shape.key_map.get(col) for col in columns]
        if all(s is not None for s in slots):
            all_uniform = all(r.shape is sample_shape for r in value.storage)
            if all_uniform:
                out_shape = _record_shape(tuple(columns))
                return Value._list_owned([Value._from_shape(out_shape, [r.storage[s] for s in slots])
                                   for r in value.storage])
    rows = []
    for _, row in elements(value):
        entries = []
        for column in columns:
            if row.has(column):
                entries.append((column, row.get(column)))
        rows.append(Value._from_entries_owned(entries))
    return Value._list_owned(rows)


define('SELECT_COLS', 2, INF, fn=_select_cols)


def _dedupe(args, ctx):
    value = args.val(0)
    if value.is_null():
        return Value._list_owned([])
    buckets = {}
    out = []
    # A value with no children is identified by its kind and text alone (EQL compares
    # nothing else of it, and a number is its canonical text), so a plain set decides
    # it with the interpreter's own hash and equality instead of a structural hash and
    # an eql() per element (PY-P26). Values with children take the structural path;
    # the two domains cannot be EQL to each other (different sizes).
    scalars = set()
    for _, item in elements(value):
        if item.size() == 0:
            ident = (item.kind, item.scalar)
            if ident in scalars:
                continue
            scalars.add(ident)
            out.append(item)
            continue
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
    return Value._list_owned(out)


define('DISTINCT', 1, 1, fn=_dedupe)
define('DEDUPE', 1, 1, fn=_dedupe)


# --- relational links ------------------------------------------------------

def expr_depends_only_on(node, allowed):
    """Iterative for the reason node_contains_var is (builtins/aggregate.py): a
    join predicate can be a chain as deep as the source is long."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        t = n.t
        if t == 'var':
            if _upper_name(n.name) not in allowed:
                return False
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
    return True


def try_extract_equi_keys(node, b1, b2):
    if node is None or node.t != 'bin' or node.op not in ('==', '$=='):
        return None
    # The right binder shadows the left when both are spelled alike (SPEC 7.4):
    # every occurrence names the right element, so neither operand is "the left
    # side" and no key can be extracted -- the general path decides.
    if _upper_name(b1) == _upper_name(b2):
        return None
    left_names = {_upper_name(b1), _upper_name(b1.lower()), '_1', '_'}
    right_names = {_upper_name(b2), _upper_name(b2.lower()), '_2'}
    if expr_depends_only_on(node.l, left_names) and expr_depends_only_on(node.r, right_names):
        return node.l, node.r, node.op == '==', False
    if expr_depends_only_on(node.r, left_names) and expr_depends_only_on(node.l, right_names):
        return node.r, node.l, node.op == '==', True
    return None


def single_relation_name(node):
    if node is None:
        return None
    if node.t == 'var':
        return node.name
    if node.t == 'call' and node.args and node.name not in ('LINK', 'LINK_LEFT'):
        return single_relation_name(node.args[0])
    return None


class _JoinBad:
    """A key the comparison rejects: the pair it meets must raise, as the
    comparison would (review 2026-09-25 SEM-06)."""
    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value


def canonical_join_key(value, numeric):
    """An equi-join key: the value as the comparison would compare it (spec
    §7.4) -- `==` through as_decimal, `$==` through as_bytes, the coercions the
    evaluator uses -- so a BIN meets the TEXT of its bytes and a list its
    scalar. NULL is never compared (None); a value the coercion rejects is a
    _JoinBad rather than "no match"."""
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
                    except ValueError:      # over the interpreter's int digit limit
                        pass
            try:
                d = value.as_decimal()
            except SelError:
                return _JoinBad(value)

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
    try:
        return value.as_bytes()
    except SelError:
        return _JoinBad(value)


def _bucket_join_key(buckets, facts, key, row):
    """Bucket a good right key; remember whether any key is live (not NULL),
    the first live key if it was rejected, and the first rejected one."""
    if key is None:
        return
    if isinstance(key, _JoinBad):
        if not facts['live']:
            facts['live_bad'] = key.value
        if facts['bad'] is None:
            facts['bad'] = key.value
    else:
        buckets.setdefault(key, []).append(row)
    facts['live'] = True


def _check_join_pair(equi, key, facts):
    """A left key meets the right keys pair by pair, in order, as the
    comparison would: a rejected left key raises against the first live right
    key, a good one against the first rejected right key -- the operator's
    left operand coerced first. NULLs are never compared."""
    if key is None or not facts['live']:
        return
    left_expr, right_expr, numeric, swapped = equi
    if isinstance(key, _JoinBad):
        if swapped and facts['live_bad'] is not None:
            _coerce_join_operand(numeric, facts['live_bad'], right_expr)
        _coerce_join_operand(numeric, key.value, left_expr)
    if facts['bad'] is not None:
        _coerce_join_operand(numeric, facts['bad'], right_expr)


def _coerce_join_operand(numeric, value, node):
    if numeric:
        value.as_decimal(node.pos)
    else:
        value.as_bytes(node.pos)
    raise AssertionError('a rejected join key did not raise')


# Flat bounded ownership prevents alias layouts retaining chains of other caches.
_ALIAS_PLANS = {}


# `_1` and `_2` name a position, not a relation: an argument with no name is
# bound bare (spec §7.4).
def is_positional_binder(name):
    return name == '_1' or name == '_2'


def ensure_row_table_alias(row, table_name):
    if not table_name or is_positional_binder(table_name) or row.has(table_name):
        return row
    lower = table_name.lower()
    if row.shape is not None:
        old_shape = row.shape
        cache_key = (old_shape, table_name)
        cached = _ALIAS_PLANS.get(cache_key)
        if cached is None:
            add_lower = lower != table_name and lower not in old_shape.key_map
            keys = tuple(list(old_shape.keys) + [table_name] + ([lower] if add_lower else []))
            target_shape = _record_shape(keys)
            cached = (target_shape, add_lower)
            if len(keys) <= 256 and sum(map(len, keys)) <= 16384:
                if len(_ALIAS_PLANS) >= 256:
                    _ALIAS_PLANS.clear()
                _ALIAS_PLANS[cache_key] = cached
        target_shape, add_lower = cached
        storage = [*row.storage, row, row] if add_lower else [*row.storage, row]
        return Value._from_shape(target_shape, storage)
    entries = row.entries()
    entries.append((table_name, row))
    if lower != table_name and not row.has(lower):
        entries.append((lower, row))
    return Value._from_entries_owned(entries)


def make_null_record(sample, table_name):
    """An unmatched LINK_LEFT row's right side (spec §7.4): shaped like the
    first right element as bound -- SAMPLE, already extended with the name --
    every field NULL; with no right elements, just the name keys; with no name
    either, NULL."""
    entries = []
    seen = set()
    if sample is not None:
        for key in sample.keys():
            entries.append((key, Value.none()))
            seen.add(key)
    if sample is None and table_name and not is_positional_binder(table_name):
        for name in (table_name, table_name.lower()):
            if name not in seen:
                entries.append((name, Value.none()))
                seen.add(name)
    return Value._from_entries_owned(entries)


def is_nested_record(value):
    return value.size() > 0 and not value.is_list


# A field's category for the joined row (spec §7.4): a nested record (a record
# with a field) is carried, anything else is a scalar field -- and a right
# scalar that is NULL is not promoted.
_SCALAR, _NULL, _NESTED = 0, 1, 2


def _category(value):
    if value.kind != NONE or value.is_list:
        return _SCALAR
    return _NESTED if value.size() > 0 else _NULL


def make_joined_row(left, right, b1, b2, null_right):
    """One joined row, from THIS pair's two elements (spec §7.4): the nested
    records the left element carries, the left binders, the right binders,
    then the left element's scalar fields whose names (ASCII-case-
    insensitively) are not the right element's, then the right element's
    non-NULL scalar fields whose names are not the left's -- each key once,
    where it first occurred, a binder key holding the row this LINK bound.
    RIGHT is None for an unmatched LINK_LEFT row, whose right element is
    NULL_RIGHT and promotes nothing."""
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

    left_entries = left.entries()
    for key, value in left_entries:
        if _category(value) == _NESTED:
            put(key, value)
    for name in _binder_keys(b1, '_1'):
        bind(name, left)
    rside = right if right is not None else (null_right if null_right is not None else Value.none())
    for name in _binder_keys(b2, '_2'):
        bind(name, rside)
    right_entries = rside.entries() if rside.size() > 0 and not rside.is_list else []
    right_names = {_upper_name(key) for key, _ in right_entries}
    for key, value in left_entries:
        if _category(value) != _NESTED and _upper_name(key) not in right_names:
            put(key, value)
    if right is not None:
        left_names = {_upper_name(key) for key, _ in left_entries}
        for key, value in right_entries:
            if _category(value) == _SCALAR and _upper_name(key) not in left_names:
                put(key, value)
    return Value._from_entries_owned(entries)


def _binder_keys(name, positional):
    keys = [name]
    lower = name.lower()
    if lower != name:
        keys.append(lower)
    if name != positional:
        keys.append(positional)
    return keys


def _row_plan(left, rside, matched, b1, b2):
    """The joined row of a pair as a plan over the two elements' storage, for
    every pair whose elements have these shapes and field categories: the
    output shape, and per output key where its value comes from -- ('L', i)
    left slot, ('R', i) right slot, ('LS',) the left element, ('RS',) the
    right one. make_joined_row is the rule; this is it, compiled."""
    keys = []
    ops = []
    slot = {}

    def put(key, op):
        if key in slot:
            return
        slot[key] = len(keys)
        keys.append(key)
        ops.append(op)

    def bind(key, op):
        if key in slot:
            ops[slot[key]] = op
        else:
            put(key, op)

    lkeys = left.shape.keys
    lcat = [_category(v) for v in left.storage]
    for i, key in enumerate(lkeys):
        if lcat[i] == _NESTED:
            put(key, ('L', i))
    for name in _binder_keys(b1, '_1'):
        bind(name, ('LS',))
    for name in _binder_keys(b2, '_2'):
        bind(name, ('RS',))
    rkeys = rside.shape.keys if rside.shape is not None else ()
    right_names = {_upper_name(k) for k in rkeys}
    for i, key in enumerate(lkeys):
        if lcat[i] != _NESTED and _upper_name(key) not in right_names:
            put(key, ('L', i))
    if matched:
        rcat = [_category(v) for v in rside.storage]
        left_names = {_upper_name(k) for k in lkeys}
        for i, key in enumerate(rkeys):
            if rcat[i] == _SCALAR and _upper_name(key) not in left_names:
                put(key, ('R', i))
    return _record_shape(tuple(keys)), ops


def _left_nested(v):
    """The one fact about a left field that decides a joined row: a nested
    record is carried first, anything else (NULL included) is promoted."""
    return v.kind == NONE and not v.is_list and v.size() > 0


def _compile_plan(plan, left, rside, matched, b1, b2):
    """The plan as one function of (left, rside, left_ok): the row, or None
    when the pair breaks what the plan assumed. Of the left element, whether
    each field is a nested record (checked unless LEFT_OK, once per left row);
    of the right, that each field it promotes is a non-NULL scalar and that
    each field it left out for being NULL or a record still is one. A right
    field the left names, or named like a binder key, is never promoted and
    needs no check. (Python checks the right fields pair by pair: a pass over
    the right rows to learn they are all flat, as the other hosts make, costs
    more here than the checks it saves, and nothing where each right row is
    paired once.)

    Beside it, many(left, rights, i, out, project): the rows of LEFT with
    rights[i:], appended to OUT in one loop, for a LEFT this plan already
    fits -- one call per left row instead of one per pair. A pair the plan
    does not fit goes to PROJECT, and the loop goes on with the next."""
    shape, ops = plan
    lnested = [_left_nested(v) for v in left.storage]
    # Inline, and a shaped record's size read off its shape: no calls for the
    # common values -- text and shaped records.
    lscalar, lrecord = [], []
    for i, nested in enumerate(lnested):
        x = f'l_s[{i}]'
        if nested:
            lrecord.append(f'({x}.shape.size > 0 if {x}.shape is not None else '
                           f'({x}.kind == "NONE" and not {x}.is_list and {x}.size() > 0))')
        else:
            lscalar.append((f'{x}.kind', f'({x}.kind != "NONE" or {x}.is_list or '
                                         f'({x}.shape.size == 0 if {x}.shape is not None else {x}.size() == 0))'))
    promoted = {op[1] for op in ops if op[0] == 'R'}
    rscalar = [(f'r_s[{i}].kind', f'(r_s[{i}].kind != "NONE" or r_s[{i}].is_list)') for i in sorted(promoted)]
    rnull = []
    if matched:
        left_names = {_upper_name(k) for k in left.shape.keys}
        binder_names = set(_binder_keys(b1, '_1')) | set(_binder_keys(b2, '_2'))
        for i, k in enumerate(rside.shape.keys):
            v = rside.storage[i]
            if (i not in promoted and _upper_name(k) not in left_names and k not in binder_names
                    and v.kind == NONE and not v.is_list):
                rnull.append(f'(r_s[{i}].kind == "NONE" and not r_s[{i}].is_list)')
    lguard = ' and '.join(_scalars(lscalar) + lrecord) or 'True'
    rguard = ' and '.join(_scalars(rscalar) + rnull) or 'True'
    items = []
    for op in ops:
        tag = op[0]
        items.append(f'l_s[{op[1]}]' if tag == 'L' else f'r_s[{op[1]}]' if tag == 'R'
                     else 'left' if tag == 'LS' else 'rside')
    row = f"_from_shape(_shape, [{', '.join(items)}])"
    code = (f"def build(left, rside, left_ok):\n"
            f"    l_s = left.storage\n    r_s = rside.storage\n"
            f"    if not left_ok and not ({lguard}):\n        return None\n"
            f"    if not ({rguard}):\n        return None\n"
            f"    return {row}\n"
            f"def many(left, rights, i, out, project):\n"
            f"    append = out.append\n    l_s = left.storage\n"
            f"    for rside in rights[i:]:\n"
            f"        if rside.shape is _rshape:\n"
            f"            r_s = rside.storage\n"
            f"            if {rguard}:\n"
            f"                append({row})\n"
            f"                continue\n"
            f"        append(project(left, rside))\n")
    # The functions land in a namespace of their own, not in their globals: a
    # function held by its own globals is a reference cycle, garbage for the
    # collector every run pays for.
    ns = {'_from_shape': Value._from_shape, '_shape': shape, '_rshape': rside.shape}
    local = {}
    exec(_plan_code(code), ns, local)
    return local['build'], local['many']


# The generated source depends only on the op list and the guards -- indices and
# tests, never on names or shape objects (those arrive in the namespace) -- so two
# layouts that differ only in their key names, or the same layout met again after
# its shape was evicted, generate the same text. Compiling it is what cost: a LINK
# inside a MAP body, or rows with one layout apiece, compiled a plan per call.
# Process-wide and bounded like _ALIAS_PLANS; a racing eviction or insert only
# costs a recompile.
_PLAN_CODE = {}
_PLAN_CODE_ENTRIES = 512


def _plan_code(source):
    code = _PLAN_CODE.get(source)
    if code is None:
        code = compile(source, '<join-plan>', 'exec')
        if len(_PLAN_CODE) >= _PLAN_CODE_ENTRIES:
            _PLAN_CODE.clear()
        _PLAN_CODE[source] = code
    return code


def _scalars(tests):
    """TESTS, (kind, test) per field that must not be a record, as guard
    terms: with two or more, one membership test of their kinds first -- no
    field of kind NONE passes them all at once, and only a row where one is
    (a list, a NULL, a record) pays for the tests one by one."""
    if len(tests) < 2:
        return [test for _, test in tests]
    return [f'("NONE" not in ({", ".join(kind for kind, _ in tests)}) or '
            f'({" and ".join(test for _, test in tests)}))']


def make_join_projector(b1, b2, null_right):
    """project(left, right): make_joined_row, through compiled plans. For
    each pair of shapes the plans built so far are tried in turn; each checks
    the facts it assumed (_compile_plan) and a pair none fits gets its own
    plan from make_joined_row's rule (_row_plan). A left row's matches come
    one after another, so the last pair's plan is tried first, checking the
    left row only when it is a new one.

    project_many(left, rights, out): the rows of LEFT with each of RIGHTS
    (none of them None), appended to OUT -- the first through project, the
    rest through the loop compiled beside the plan that fit it."""
    plans = {}
    manys = {}
    last_left = last_build = pair_l = pair_r = pair_candidates = None
    pair_matched = False

    def project(left, right):
        nonlocal last_left, last_build, pair_l, pair_r, pair_matched, pair_candidates
        if right is not None:
            rside = right
            matched = True
        else:
            rside = null_right
            matched = False
        lshape = left.shape
        if lshape is None or rside is None or rside.shape is None:
            return make_joined_row(left, right, b1, b2, null_right)
        if lshape is pair_l and rside.shape is pair_r and matched is pair_matched:
            row = last_build(left, rside, left is last_left)
            if row is not None:
                last_left = left
                return row
            candidates = pair_candidates
        else:
            key = (lshape, rside.shape, matched)
            candidates = plans.get(key)
            if candidates is None:
                candidates = plans[key] = []
        for build in candidates:
            row = build(left, rside, False)
            if row is not None:
                break
        else:
            build, manys[build] = _compile_plan(_row_plan(left, rside, matched, b1, b2),
                                                left, rside, matched, b1, b2)
            candidates.append(build)
            row = build(left, rside, True)
        last_left, last_build = left, build
        pair_l, pair_r, pair_matched, pair_candidates = lshape, rside.shape, matched, candidates
        return row

    def project_many(left, rights, out):
        out.append(project(left, rights[0]))
        if len(rights) > 1:
            # A plan fits LEFT when the last pair was LEFT's (the general
            # path, for a shapeless side, leaves the memo as it was).
            many = manys.get(last_build) if last_left is left else None
            if many is not None:
                many(left, rights, 1, out, project)
            else:
                for right in rights[1:]:
                    out.append(project(left, right))
    return project, project_many


def _pure_source(node):
    """Whether evaluating NODE can be observed only through its value: no
    assignment, no sequence, no call outside the shipped builtins (an
    application's own function may do anything), no ABORT. Such a node may be
    evaluated out of order -- the right source of a join before the left --
    which is what lets a FILTER's conjuncts travel down a chain of joins."""
    if node is None:
        return True
    t = node.t
    if t in ('var', 'num', 'text', 'bool'):
        return True
    if t == 'index':
        return _pure_source(node.obj) and _pure_source(node.idx)
    if t == 'bin':
        return _pure_source(node.l) and _pure_source(node.r)
    if t == 'un':
        return _pure_source(node.x)
    if t == 'list':
        return all(_pure_source(item) for item in node.items)
    if t == 'call':
        spec = node.spec
        module = getattr(getattr(spec, 'fn', None), '__module__', '') or ''
        if not module.startswith('sel.builtins') or spec.name == 'ABORT':
            return False
        return all(_pure_source(arg) for arg in node.args)
    return False


def _stage_walk(stages, owned_here, total_here, right_here=None):
    """The conjuncts a join may test before it joins, in stage order (SEL-0052,
    SEL-0054).

    Walking the conjuncts in the order the FILTERs run: one whose fields are
    all owned by the left rows (``owned_here``) is applied to them; one that
    reads only through this join's right binder (``right_here``,
    `_["products"]["is_active"]`) is applied to the right rows; one that
    reads a field of some right side -- this join's or one above -- ends the
    walk, since AND short-circuits left to right and a later conjunct may not
    run before it, UNLESS it is TOTAL here (``total_here``: it cannot raise
    on any joined row), in which case it is deferred to the join above and
    the walk goes on. A conjunct that reads anything but fields ends the walk
    too. Each stage is judged against the joins between its FILTER and this
    join (its third member, the count of them): a FILTER in the middle of a
    chain reads rows no join above it has touched.

    Returns (applied, stop): (conjunct, fields, binder, stage, right) for each
    conjunct to apply, and where the walk ended -- (stage index, conjunct
    index) -- or None when it did not. A deferral relies on no lower relation
    carrying the field; the join that has those rows repeats the walk with
    them, so a shadowed deferral stops the walk there before anything after
    it is applied.
    """
    applied = []
    for si, stage in enumerate(stages):
        for ci, (conjunct, fields, total, binder) in enumerate(stage[1]):
            if fields is not None and owned_here(fields, stage):
                applied.append((conjunct, fields, binder, stage, False))
                continue
            if fields is not None and right_here is not None and right_here(fields, stage):
                applied.append((conjunct, fields, binder, stage, True))
                continue
            if total is not None and total_here(total, stage):
                continue
            return applied, (si, ci)
    return applied, None


def _truncate_stages(stages, stop):
    """The stages up to where the walk stopped: the conjunct there and every
    one after it cannot be asked of rows below."""
    if stop is None:
        return stages
    si, ci = stop
    out = [stage for stage in stages[:si]]
    if ci:
        binder, conjuncts, above = stages[si]
        out.append((binder, conjuncts[:ci], above))
    return out


def _read_self(node, names, binder):
    """NODE with every `r["orders"]` -- a read through the left binder's own
    name (NAMES upper-cased) on the element BINDER -- replaced by the element
    itself: on the left rows the joined row's member of that name is the row."""
    if node is None:
        return None
    if (node.t == 'index' and node.obj is not None and node.obj.t == 'var' and node.obj.name == binder
            and node.idx is not None and node.idx.t == 'text' and _upper_name(node.idx.v) in names):
        return Node('var', node.pos, name=binder)
    copy = Node(node.t, node.pos)
    for slot in Node.__slots__:
        if slot in ('t', 'pos'):
            continue
        setattr(copy, slot, getattr(node, slot))
    # A compiled plan or cached slot of the original would read the original.
    copy.math_plan = None
    copy._cached_slot = None
    copy.args = [_read_self(a, names, binder) for a in node.args]
    copy.items = [_read_self(a, names, binder) for a in node.items]
    for slot in ('l', 'r', 'x', 'obj', 'idx', 'target', 'value'):
        child = getattr(node, slot)
        if child is not None:
            setattr(copy, slot, _read_self(child, names, binder))
    return copy


def _first_keys(value):
    first = first_collection_item(value)
    return {_upper_name(k) for k in first.keys()} if first is not None else set()


def _row_fact(value, name, kind):
    """Whether every row of VALUE carries the field NAME (as written) as text
    (a number is text), or, for kind NUM, as a number; for kind ANY, as any
    non-null scalar (what a promoted join key needs)."""
    for row in iter_values(value):
        v = row.get(name)
        if kind == 'ANY':
            if v is None or v.is_null() or is_nested_record(v):
                return False
            continue
        if v is None or v.kind != TEXT:
            return False
        if kind == 'NUM':
            try:
                v.as_decimal()
            except SelError:
                return False
    return True


class _SideFacts:
    """What the guard check knows about one side's rows: the union of its
    keys (upper-cased), the keys of its first row (a field every row carries
    is on the first one: a cheap refusal before a scan), per-field presence
    and kind on demand, and whether its rows may be null-extended (the right
    side of a LINK_LEFT)."""
    __slots__ = ('value', 'keys', 'first', 'nullable', 'names', '_facts')

    def __init__(self, value, keys, nullable, names=()):
        self.value = value
        self.keys = keys
        self.first = _first_keys(value)
        self.nullable = nullable
        # The member names the side's row is bound under in a joined row --
        # the binder and its lower-case alias, never a positional `_1`/`_2`,
        # which later joins rebind.
        self.names = {n for b in names if not is_positional_binder(b) for n in (b, b.lower())}
        self._facts = {}

    def present(self, name):
        """The field NAME (as written) is a key of every row, whatever its
        value: reading it through the side's member cannot raise. A
        null-extended row (LINK_LEFT) carries the first row's keys."""
        fact = self._facts.get((name, 'PRESENT'))
        if fact is None:
            # Rows sharing one record shape answer from the shape after an
            # identity scan, as _row_keys does.
            storage = self.value.storage if self.value.is_list else None
            first = storage[0].shape if storage else None
            if first is not None and all(row.shape is first for row in storage):
                fact = name in first.key_map
            else:
                rows = list(iter_values(self.value))
                fact = bool(rows) and all(row.get(name) is not None for row in rows)
            self._facts[(name, 'PRESENT')] = fact
        return fact

    def total(self, name, kind):
        # The field is on this side's first row (a cheap refusal: a field on
        # every row is on the first), on every row, with the kind the operator
        # takes.
        if _upper_name(name) not in self.first or self.nullable:
            return False
        fact = self._facts.get((name, kind))
        if fact is None:
            fact = _row_fact(self.value, name, kind)
            self._facts[(name, kind)] = fact
        return fact


def _keys_safe(obligations, left, right, above, left_names):
    """Whether every handed-down join key -- the left key of each join above
    this one that handed its conjuncts down -- cannot raise on a joined row
    built from a left row dropped here (SEL-0052). As written, those joins
    compute the key for every row they receive; a row dropped below never
    reaches them, so an E_NO_KEY there would be lost. Canonical keys never
    raise, only the reads do, so presence suffices:

      * `r["m"]["f"]` reads member m -- a relation below the join, always a
        member of its rows -- and m's field f: f must be a key of every row
        of m's relation;
      * `r["f"]` reads a promoted field: f must be carried, non-null, by
        every row of the one side below the join that has it (as totality
        asks, with any kind).

    Anything else is not proved, and nothing is dropped."""
    for key, row_names, n_outer in obligations:
        below = above[:len(above) - n_outer]
        if (key is None or key.t != 'index' or key.idx is None or key.idx.t != 'text'
                or key.obj is None):
            return False
        field = key.idx.v
        obj = key.obj
        if (obj.t == 'index' and obj.obj is not None and obj.obj.t == 'var' and obj.obj.name in row_names
                and obj.idx is not None and obj.idx.t == 'text'):
            member = obj.idx.v
            if member in left_names:
                if not left.present(field):
                    return False
                continue
            side = next((s for s in [right, *below] if member in s.names), None)
            if side is not None:
                if not side.present(field):
                    return False
                continue
            # A member carried inside the left rows (a relation joined below
            # them): read it on every left row.
            for row in iter_values(left.value):
                inner = row.get(member)
                if inner is None or inner.get(field) is None:
                    return False
            continue
        if obj.t == 'var' and obj.name in row_names:
            if not _totality([(field, 'ANY')], left, right, below):
                return False
            continue
        return False
    return True


def _totality(reqs, left, right, above):
    """Whether every (field, kind) requirement is met over the joined rows of
    this join: exactly one side -- the left rows, this right side, or a right
    side above -- carries the field at all, and that side carries it on every
    row with the kind. A field two sides carry is promoted from neither (spec
    §7.4) and the read would raise; a field of no side would too."""
    for name, kind in reqs:
        key = _upper_name(name)
        owners = [side for side in [left, right, *above] if side is not None and key in side.keys]
        if len(owners) != 1 or not owners[0].total(name, kind):
            return False
    return True


class _KeySet:
    """The upper-cased keys of every row of a side, gathered as the rows go
    by. Rows of a dense list mostly share one record shape, and a shape's keys
    are read once: the per-row cost is then one identity check, not a list of
    upper-cased strings, which is what made the pre-filter's bookkeeping
    visible in a profile of a 90,000-row join."""
    __slots__ = ('keys', 'shapes')

    def __init__(self):
        self.keys = set()
        self.shapes = set()

    def add(self, row):
        shape = row.shape
        if shape is not None:
            if id(shape) in self.shapes:
                return
            self.shapes.add(id(shape))
            self.keys.update(_upper_name(k) for k in shape.keys)
        else:
            self.keys.update(_upper_name(k) for k in row.keys())


def _row_keys(value, bound=()):
    # A dense list whose rows all share one record shape -- the common case for
    # rows built from one source -- answers from that shape after one identity
    # scan, which is a third of the cost of visiting each row. BOUND: the names
    # the side's row is bound under in the joined row (`products`, `_2`), keys
    # the side contributes as much as its fields.
    storage = value.storage if value.is_list else None
    if storage:
        first = storage[0].shape
        if first is not None and all(row.shape is first for row in storage):
            return {_upper_name(k) for k in first.keys} | {_upper_name(b) for b in bound}
    keys = _KeySet()
    for row in iter_values(value):
        keys.add(row)
    return keys.keys | {_upper_name(b) for b in bound}


def _link(args, ctx, left_join):
    # Taken before anything else is evaluated, so a LINK nested in this one's
    # sources cannot pick it up by accident (aggregate.py, _filter); it is
    # handed down on purpose below.
    prefilter = ctx.join_prefilter
    ctx.join_prefilter = None
    count = args.count()             # 3 or 5: the manifest's arity, checked at compile time
    # With conjuncts to pre-apply and a left source that is itself a join, the
    # right source is evaluated first -- unobservable when both sources are
    # pure -- so that the conjuncts still valid above this join's right rows
    # can travel down to the join below, and from there to the base rows,
    # where dropping a row saves every join above it. This is what the old
    # physical pushdown achieved by reading the context's first row at plan
    # time; done here it reads every row, at run time, and the tree stays the
    # same for any data (SEL-0049).
    right_side = None
    left_node, right_node = args.node(0), args.node(1)
    stages, deep, above, obligations = prefilter if prefilter is not None else ([], False, [], [])
    if count == 3:
        jb1 = single_relation_name(left_node) or '_1'
        jb2 = single_relation_name(right_node) or '_2'
        jpred = args.node(2)
    else:
        jb1, jb2, jpred = args.symbol(2), args.symbol(3), args.node(4)
    jequi = try_extract_equi_keys(jpred, jb1, jb2)
    # The keys a side contributes to the joined row include the names its
    # row is bound under: `_["products"]` after LINK(PRODUCTS, ...) is the
    # right row, not a field of the left ones.
    b2_names = (args.symbol(3), '_2') if count == 5 else (single_relation_name(right_node) or '_2', '_2')
    b1_names = (args.symbol(2), '_1') if count == 5 else (single_relation_name(left_node) or '_1', '_1')
    # The upper-cased keys of the joins between a stage's FILTER and this
    # join, per count of them.
    above_keys_cache = {}
    def above_keys(stage):
        n = stage[2]
        keys = above_keys_cache.get(n)
        if keys is None:
            keys = above_keys_cache[n] = set().union(*(side.keys for side in above[:n])) if n else set()
        return keys
    if (deep and stages and jequi is not None and left_node is not None and left_node.t == 'call'
            and left_node.name in ('LINK', 'LINK_LEFT', 'FILTER')
            and _pure_source(left_node) and _pure_source(right_node)):
        try:
            right_value = args.val(1)
        except SelError:
            # The right source went first for the prefilter's sake, which is only
            # unobservable while neither source raises (SPEC 7.4: as written, the
            # left source runs first). The left source is pure too: run it now with
            # nothing handed down. If it raises, ITS error is the one as written;
            # if it does not, the right source's error stands.
            args.val(0)
            raise
        right_side = _SideFacts(right_value, _row_keys(right_value, b2_names), left_join, b2_names)
        # What the rows below may still be asked, with the left rows unknown:
        # a field no right side (here or above) carries is theirs; a conjunct
        # on a right side's field is deferred only when total on that side
        # alone. The join below repeats the walk with its own sides at hand,
        # and this one again once its left rows are known (below), so a
        # deferral a lower relation's field would shadow is caught where the
        # rows are, before anything after it is applied there.
        def owned_below(fields, stage):
            return not (fields & right_side.keys) and not (fields & above_keys(stage))
        def total_below(reqs, stage):
            return _totality(reqs, None, right_side, above[:stage[2]])
        _, stop = _stage_walk(stages, owned_below, total_below)
        # Below this join, every stage has one more join above it: this one.
        handed = [(b, c, n + 1) for b, c, n in _truncate_stages(stages, stop)]
        if handed:
            # This join computes its left key on every row it receives; a row
            # dropped below never arrives, so the key is handed down as an
            # obligation for the join that drops to prove (_keys_safe).
            own_key = (jequi[0], {jb1, jb1.lower(), '_1', '_'}, len(above) + 1)
            ctx.join_prefilter = (handed, True, [right_side, *above], [own_key, *obligations])
        try:
            left_value = args.val(0)
        finally:
            ctx.join_prefilter = None
    else:
        left_value = args.val(0)
        right_value = args.val(1)
    # The join below, if it applied some of these conjuncts, says which ones
    # (by identity) every row that came up has passed; those are skipped here
    # unless a row was kept on an error below, since such a row must reach
    # the FILTER untouched and cannot be told apart from the others.
    below = ctx.join_prefilter_report
    ctx.join_prefilter_report = None
    # Drops below that left this join no left rows: as written it may have
    # had some, and then it computes every right key (and raises where one
    # cannot be) before it finds that no row survives. Only the rows as
    # written can say, so the left side -- pure, or nothing was handed down --
    # is evaluated again without them, and this join runs as written.
    if below is not None and below[2] and first_collection_item(left_value) is None:
        left_value = args.eval_node(args.node(0))
        ctx.join_prefilter_report = None
        below = None
    applied_below = below[0] if (below is not None and not below[1]) else set()
    dropped = [below is not None and below[2]]
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
        return Value._list_owned([])

    # Each side is listed ONCE, here (SPEC 7.3 snapshot): the right side is walked
    # again for every left row, and a predicate that grows or replaces a side must
    # neither extend the walk nor change the rows later left rows see.
    left_items = list(iter_values(left_value))
    right_items = list(iter_values(right_value))
    first_left = first_collection_item(left_value)
    first_right = first_collection_item(right_value)
    if first_left is None or first_right is None:
        if not left_join or first_left is None:
            return Value._list_owned([])
    # Every element is extended with its side's name (ensure_row_table_alias
    # skips one that already has the key), and every row is built from its
    # own pair (spec §7.4): nothing is decided from a first element except
    # the shape of LINK_LEFT's null record.
    needs_left_alias = bool(b1 and b1 != '_1')
    needs_right_alias = bool(b2 and b2 != '_2')
    sample_right = ensure_row_table_alias(first_right, b2) if (first_right is not None and needs_right_alias) else first_right
    null_right = make_null_record(sample_right, b2) if left_join else None
    if null_right is not None and null_right.is_null():
        null_right = None
    project, project_many = make_join_projector(b1, b2, null_right)
    equi = try_extract_equi_keys(predicate, b1, b2)
    output = []

    # The pre-filter, decided at run time from the rows themselves. A leading
    # conjunct reading `_["F"]` may be applied to a left row before the join
    # only when F is a key of NO right row (over every row, not a sample), so
    # that the joined row's F is the left row's F; a conjunct with any field
    # the right side has ends the usable prefix, since AND short-circuits left
    # to right and a later conjunct may not run before an earlier one. On a
    # left row it evaluates FALSE the row is dropped -- the joined rows it
    # would have produced (or its null-extended row, for LINK_LEFT) would all
    # have been dropped by the same conjunct. On an error the row is KEPT: the
    # full predicate runs over the joined rows afterwards and raises there, in
    # row order, or does not raise at all for a left row that joins nothing.
    gather = _KeySet() if (prefilter is not None and right_side is None) else None
    prefix = []
    right_prefix = []
    # How many left conjuncts come before the first right one: with a right
    # row kept on an error, a later left conjunct may not drop a left row --
    # the joined row would have raised in the right conjunct first.
    left_before_right = [-1]
    if prefilter is not None:
        binders = [stage[0] for stage in stages]
        applied_ids = set()
        errored = [False]
        self_names = {_upper_name(b1), '_1'}
        # A read through this join's right binder is the right element in
        # every joined row -- the binder is bound last (spec §7.4) -- unless
        # the left binder has the same name, or a join above rebinds it.
        def right_names(stage):
            return {_upper_name(b2), '_2'} if stage[2] == 0 else {_upper_name(b2)}
        right_here = None
        if not left_join and _upper_name(b1) != _upper_name(b2):
            def right_here(fields, stage):
                return fields <= right_names(stage) and not (fields & above_keys(stage))
        def settle_prefix():
            # With both sides at hand: a field no right side carries is the
            # left rows' (a read through the left binder's own name,
            # `_["orders"]["year"]` on ORDERS rows, is the row itself, spec
            # §7.4); a right side's field may be passed over only when the
            # conjunct is total over this join's rows.
            left_side = _SideFacts(left_value, _row_keys(left_value, b1_names), False, b1_names)
            if obligations and not _keys_safe(obligations, left_side, right_side, above,
                                              {n for b in b1_names if not is_positional_binder(b)
                                               for n in (b, b.lower())}):
                return
            def owned_here(fields, stage):
                # A joined row carries a left element's field exactly as the
                # element does whenever no right element has the name (§7.4,
                # pair by pair) -- and no row depends on another, so a drop
                # below changes nothing above but the rows it drops.
                return not (fields & right_side.keys) and not (fields & above_keys(stage))
            def total_here(reqs, stage):
                return _totality(reqs, left_side, right_side, above[:stage[2]])
            applied, _stop = _stage_walk(stages, owned_here, total_here, right_here)
            for conjunct, fields, binder, stage, right in applied:
                applied_ids.add(id(conjunct))
                if id(conjunct) in applied_below:
                    continue
                if right:
                    if left_before_right[0] < 0:
                        left_before_right[0] = len(prefix)
                    right_prefix.append(_read_self(conjunct, right_names(stage), binder))
                    continue
                if fields & self_names and not (fields & left_side.first):
                    conjunct = _read_self(conjunct, self_names, binder)
                prefix.append(conjunct)
        def verdict(conjuncts, row, frame):
            """0: keep the row; 1: drop it; 2: keep it, a conjunct raised on it.
            The frame names the row by every stage's binder."""
            for binder in binders:
                frame[binder] = row
            for conjunct in conjuncts:
                try:
                    keep = args.eval_node(conjunct).as_bool(conjunct.pos)
                except SelError:
                    errored[0] = True
                    return 2
                if not keep:
                    return 1
            return 0

    if equi is not None and sample_right is not None:
        left_expr, right_expr, numeric, _swapped = equi
        buckets = {}
        facts = {'live': False, 'live_bad': None, 'bad': None}
        frame_right = {b2: None, b2.lower(): None, '_2': None}
        ctx.push_frame(frame_right)
        try:
            for item in right_items:
                row = ensure_row_table_alias(item, b2) if needs_right_alias else item
                frame_right[b2] = row
                frame_right[b2.lower()] = row
                frame_right['_2'] = row
                _bucket_join_key(buckets, facts, canonical_join_key(args.eval_node(right_expr), numeric), row)
                if gather is not None:
                    gather.add(row)
        finally:
            ctx.pop_frame()
        if prefilter is not None:
            if gather is not None:
                right_side = _SideFacts(right_value, gather.keys | {_upper_name(b) for b in b2_names}, left_join,
                                        b2_names)
            settle_prefix()
        # The right rows the right conjuncts reject, once each, after every
        # right key was computed. They stay in their buckets: a left row still
        # counts them towards the numbering, and one kept on an error joins
        # them.
        rejected = None
        if right_prefix:
            rejected = set()
            frame = {binder: None for binder in binders}
            before = errored[0]
            errored[0] = False
            ctx.push_frame(frame)
            try:
                for bucket in buckets.values():
                    for right in bucket:
                        if verdict(right_prefix, right, frame) == 1:
                            rejected.add(id(right))
            finally:
                ctx.pop_frame()
            if errored[0]:
                del prefix[left_before_right[0]:]
            errored[0] = errored[0] or before

        # A FILTER keeps its input's keys, so the rows dropped here still count
        # towards the numbering of the rows kept: the join key is computed
        # first, as it is for every row (and raises where it would have), the
        # matches say how many joined rows the dropped row stood for, and the
        # kept rows are emitted under the positions they would have had.
        numbered = bool(prefix or rejected is not None) and not deep
        keys = [] if numbered else None
        position = 1
        # The join key is computed for every left row before the pre-filter
        # is asked, so that a key that raises still raises. When the key is a
        # literal field of the row -- `_1["customer_id"]` -- the read can only
        # raise for a row that lacks the field (the evaluator's E_NO_KEY; the
        # canonical key never raises), so a row that HAS it may be rejected
        # first and its key never computed: that is most of what a pushed
        # filter used to save. Only where nothing observes the numbering.
        fast_field = None
        if (prefix and deep and left_expr.t == 'index' and left_expr.obj is not None
                and left_expr.obj.t == 'var' and left_expr.idx is not None
                and left_expr.idx.t == 'text'
                and _upper_name(left_expr.obj.name) in (_upper_name(b1), '_1', '_')):
            fast_field = left_expr.idx.v
        frame_left = {b1: None, b1.lower(): None, '_1': None, '_': None}
        if prefilter is not None:
            for binder in binders:
                frame_left.setdefault(binder, None)
        ctx.push_frame(frame_left)
        try:
            for item in left_items:
                row = ensure_row_table_alias(item, b1) if needs_left_alias else item
                asked = -1
                if fast_field is not None and row.get(fast_field) is not None:
                    asked = verdict(prefix, row, frame_left)
                    if asked == 1:
                        # Dropped before its key was computed -- but the key is
                        # this very field, and a rejected one still raises.
                        _check_join_pair(equi, canonical_join_key(row.get(fast_field), numeric), facts)
                        dropped[0] = True
                        continue
                frame_left[b1] = row
                frame_left[b1.lower()] = row
                frame_left['_1'] = row
                frame_left['_'] = row
                key = canonical_join_key(args.eval_node(left_expr), numeric)
                _check_join_pair(equi, key, facts)
                matches = buckets.get(key) if key is not None and not isinstance(key, _JoinBad) else None
                if asked < 0:
                    asked = verdict(prefix, row, frame_left) if prefix else 0
                if asked == 1:
                    dropped[0] = True
                    if numbered:
                        position += len(matches) if matches else (1 if left_join else 0)
                    continue
                if matches:
                    # A left row kept on an error meets every right row: its
                    # joined rows raise in the FILTER, in order, where they
                    # would have.
                    skip = rejected if (rejected is not None and asked == 0) else None
                    if skip is None:
                        check_collection(len(output) + len(matches), args.pos)
                        project_many(row, matches, output)
                        if keys is not None:
                            keys.extend([str(p) for p in range(position, position + len(matches))])
                        position += len(matches)
                    else:
                        for right in matches:
                            if id(right) in skip:
                                dropped[0] = True
                                position += 1
                                continue
                            check_collection(len(output) + 1, args.pos)
                            output.append(project(row, right))
                            if keys is not None:
                                keys.append(str(position))
                            position += 1
                elif left_join:
                    check_collection(len(output) + 1, args.pos)
                    output.append(project(row, None))
                    if keys is not None:
                        keys.append(str(position))
                    position += 1
        finally:
            ctx.pop_frame()
        if prefilter is not None:
            ctx.join_prefilter_report = (applied_ids, errored[0], dropped[0])
        if keys is not None and len(keys) != position - 1:
            return Value._list_owned(output, keys)
    else:
        # No pre-filter on the general join: the numbering of the kept rows
        # would need the count of matches of every dropped row, which is the
        # predicate scan the pre-filter exists to avoid.
        frame = {b1: None, b1.lower(): None, '_1': None, '_': None,
                 b2: None, b2.lower(): None, '_2': None}
        ctx.push_frame(frame)
        try:
            # The right side's table alias depends only on the right row, so it is
            # made once (on the first left row, so an empty left side still does no
            # work), not once per PAIR -- it was 21% of a 500x500 join (PY-P14).
            rights = None if needs_right_alias else right_items
            b1_lower, b2_lower = b1.lower(), b2.lower()
            eval_predicate = args.eval_node
            for left_item in left_items:
                left = ensure_row_table_alias(left_item, b1) if needs_left_alias else left_item
                frame[b1] = left
                frame[b1_lower] = left
                frame['_1'] = left
                frame['_'] = left
                matched = False
                if rights is None:
                    rights = [ensure_row_table_alias(r, b2) for r in right_items]

                for right in rights:
                    frame[b2] = right
                    frame[b2_lower] = right
                    frame['_2'] = right
                    if eval_predicate(predicate).as_bool(predicate.pos):
                        matched = True
                        check_collection(len(output) + 1, args.pos)
                        output.append(project(left, right))

                if left_join and not matched:
                    check_collection(len(output) + 1, args.pos)
                    output.append(project(left, None))
        finally:
            ctx.pop_frame()
    return Value._list_owned(output)


define('LINK', 3, 5, lazy=True, binds=True,
       fn=lambda args, ctx: _link(args, ctx, False))
define('LINK_LEFT', 3, 5, lazy=True, binds=True,
       fn=lambda args, ctx: _link(args, ctx, True))
