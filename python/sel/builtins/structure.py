
from ..errors import SelError, fail
from ..registry import INF, define
from ..parser import Node
from ..value import NONE, TEXT, Value, iter_elements, iter_values, structural_hash, _record_shape


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
    shape = args.record_shape
    if shape is not None and shape.keys == tuple(keys):
        return Value._from_shape(shape, values)
    return Value.record(keys, values)


define('RECORD', 0, INF,
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
            cached = (target_shape, old_shape.size, add_lower)
            if len(keys) <= 256 and sum(map(len, keys)) <= 16384:
                if len(_ALIAS_PLANS) >= 256:
                    _ALIAS_PLANS.clear()
                _ALIAS_PLANS[cache_key] = cached
        target_shape, old_size, add_lower = cached
        storage = [*row.storage, row, row] if add_lower else [*row.storage, row]
        return Value._from_shape(target_shape, storage)
    entries = row.entries()
    entries.append((table_name, row))
    if lower != table_name and not row.has(lower):
        entries.append((lower, row))
    return Value.from_entries(entries)


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
    return Value.from_entries(entries)


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
    right_names = {key.upper() for key, _ in right_entries}
    for key, value in left_entries:
        if _category(value) != _NESTED and key.upper() not in right_names:
            put(key, value)
    if right is not None:
        left_names = {key.upper() for key, _ in left_entries}
        for key, value in right_entries:
            if _category(value) == _SCALAR and key.upper() not in left_names:
                put(key, value)
    return Value.from_entries(entries)


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
    right_names = {k.upper() for k in rkeys}
    for i, key in enumerate(lkeys):
        if lcat[i] != _NESTED and key.upper() not in right_names:
            put(key, ('L', i))
    if matched:
        rcat = [_category(v) for v in rside.storage]
        left_names = {k.upper() for k in lkeys}
        for i, key in enumerate(rkeys):
            if rcat[i] == _SCALAR and key.upper() not in left_names:
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
    more here than the checks it saves.)"""
    shape, ops = plan
    lnested = [_left_nested(v) for v in left.storage]
    # Inline, and a shaped record's size read off its shape: no calls for the
    # common values -- text and shaped records.
    lguards = []
    for i, nested in enumerate(lnested):
        x = f'l_s[{i}]'
        if nested:
            lguards.append(f'({x}.shape.size > 0 if {x}.shape is not None else '
                           f'({x}.kind == "NONE" and not {x}.is_list and {x}.size() > 0))')
        else:
            lguards.append(f'({x}.kind != "NONE" or {x}.is_list or '
                           f'({x}.shape.size == 0 if {x}.shape is not None else {x}.size() == 0))')
    promoted = {op[1] for op in ops if op[0] == 'R'}
    rguards = [f'(r_s[{i}].kind != "NONE" or r_s[{i}].is_list)' for i in sorted(promoted)]
    if matched:
        left_names = {k.upper() for k in left.shape.keys}
        binder_names = set(_binder_keys(b1, '_1')) | set(_binder_keys(b2, '_2'))
        for i, k in enumerate(rside.shape.keys):
            v = rside.storage[i]
            if (i not in promoted and k.upper() not in left_names and k not in binder_names
                    and v.kind == NONE and not v.is_list):
                rguards.append(f'(r_s[{i}].kind == "NONE" and not r_s[{i}].is_list)')
    items = []
    for op in ops:
        tag = op[0]
        items.append(f'l_s[{op[1]}]' if tag == 'L' else f'r_s[{op[1]}]' if tag == 'R'
                     else 'left' if tag == 'LS' else 'rside')
    code = (f"def build(left, rside, left_ok):\n"
            f"    l_s = left.storage\n    r_s = rside.storage\n"
            f"    if not left_ok and not ({' and '.join(lguards) or 'True'}):\n        return None\n"
            f"    if not ({' and '.join(rguards) or 'True'}):\n        return None\n"
            f"    return _from_shape(_shape, [{', '.join(items)}])\n")
    # The function lands in a namespace of its own, not in its globals: a
    # function held by its own globals is a reference cycle, garbage for the
    # collector every run pays for.
    ns = {'_from_shape': Value._from_shape, '_shape': shape}
    local = {}
    exec(code, ns, local)
    return local['build']


def make_join_projector(b1, b2, null_right):
    """project(left, right): make_joined_row, through compiled plans. For
    each pair of shapes the plans built so far are tried in turn; each checks
    the facts it assumed (_compile_plan) and a pair none fits gets its own
    plan from make_joined_row's rule (_row_plan). A left row's matches come
    one after another, so the last pair's plan is tried first, checking the
    left row only when it is a new one."""
    plans = {}
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
            build = _compile_plan(_row_plan(left, rside, matched, b1, b2), left, rside, matched, b1, b2)
            candidates.append(build)
            row = build(left, rside, True)
        last_left, last_build = left, build
        pair_l, pair_r, pair_matched, pair_candidates = lshape, rside.shape, matched, candidates
        return row
    return project


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


def _stage_walk(stages, owned_here, total_here, pushed_held):
    """The conjuncts a join may pre-apply to its left rows, in stage order,
    and the fields whose absence below is relied on (SEL-0052).

    Walking the conjuncts in the order the FILTERs run: one whose fields are
    all owned by the left rows (``owned_here``) is applied; one that reads a
    field of some right side -- this join's or one above -- ends the walk,
    since AND short-circuits left to right and a later conjunct may not run
    before it, UNLESS it is TOTAL here (``total_here``: it cannot raise on any
    joined row), in which case it is deferred to the join above and the walk
    goes on. A conjunct that reads anything but fields ends the walk too.

    Returns (applied, stop): the (conjunct, fields) pairs to apply, and where
    the walk ended -- (stage index, conjunct index) -- or None when it did
    not. A deferral relies on no lower relation carrying the field; the join
    that has those rows repeats the walk with them, so a shadowed deferral
    stops the walk there before anything after it is applied.
    """
    applied = []
    for si, (_binder, conjuncts) in enumerate(stages):
        for ci, (conjunct, fields, total, pushed, _b) in enumerate(conjuncts):
            # A conjunct the optimiser pushed below already held on every row
            # (unless a tentative FILTER kept a row on an error: then nothing
            # after it may run before it).
            if pushed:
                if pushed_held:
                    continue
                return applied, (si, ci)
            if fields is not None and owned_here(fields):
                applied.append((conjunct, fields, _b))
                continue
            if total is not None and total_here(total):
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
        binder, conjuncts = stages[si]
        out.append((binder, conjuncts[:ci]))
    return out


def _read_self(node, names, binder):
    """NODE with every `r["orders"]` -- a read through the left binder's own
    name (NAMES upper-cased) on the element BINDER -- replaced by the element
    itself: on the left rows the joined row's member of that name is the row."""
    if node is None:
        return None
    if (node.t == 'index' and node.obj is not None and node.obj.t == 'var' and node.obj.name == binder
            and node.idx is not None and node.idx.t == 'text' and node.idx.v.upper() in names):
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
    return {k.upper() for k in first.keys()} if first is not None else set()


def _row_fact(value, name, kind):
    """Whether every row of VALUE carries the field NAME (as written) as text
    (a number is text), or, for kind NUM, as a number; for kind ANY, as any
    non-null scalar (what a promoted join key needs)."""
    for row in iter_collection_items(value):
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
                rows = list(iter_collection_items(self.value))
                fact = bool(rows) and all(row.get(name) is not None for row in rows)
            self._facts[(name, 'PRESENT')] = fact
        return fact

    def total(self, name, kind):
        # The field is on this side's first row (a cheap refusal: a field on
        # every row is on the first), on every row, with the kind the operator
        # takes.
        if name.upper() not in self.first or self.nullable:
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
            for row in iter_collection_items(left.value):
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
        key = name.upper()
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
            self.keys.update(k.upper() for k in shape.keys)
        else:
            self.keys.update(k.upper() for k in row.keys())


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
            return {k.upper() for k in first.keys} | {b.upper() for b in bound}
    keys = _KeySet()
    for row in iter_collection_items(value):
        keys.add(row)
    return keys.keys | {b.upper() for b in bound}


def _link(args, ctx, left_join):
    # Taken before anything else is evaluated, so a LINK nested in this one's
    # sources cannot pick it up by accident (aggregate.py, _filter); it is
    # handed down on purpose below.
    prefilter = ctx.join_prefilter
    ctx.join_prefilter = None
    count = args.count()
    if count not in (3, 5):
        fail('E_ARITY', f'{args.name} takes 3 or 5 arguments, got {count}', args.pos)
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
    above_keys = set().union(*(side.keys for side in above)) if above else set()
    kept_before = ctx.tentative_kept
    if (deep and stages and jequi is not None and left_node is not None and left_node.t == 'call'
            and left_node.name in ('LINK', 'LINK_LEFT', 'FILTER')
            and _pure_source(left_node) and _pure_source(right_node)):
        right_value = args.val(1)
        right_side = _SideFacts(right_value, _row_keys(right_value, b2_names), left_join, b2_names)
        # What the rows below may still be asked, with the left rows unknown:
        # a field no right side (here or above) carries is theirs; a conjunct
        # on a right side's field is deferred only when total on that side
        # alone. The join below repeats the walk with its own sides at hand,
        # and this one again once its left rows are known (below), so a
        # deferral a lower relation's field would shadow is caught where the
        # rows are, before anything after it is applied there.
        def owned_below(fields):
            return not (fields & right_side.keys) and not (fields & above_keys)
        def total_below(reqs):
            return _totality(reqs, None, right_side, above)
        # Whether the conjuncts the optimiser pushed below held so far: a
        # tentative FILTER on this join's right side has just run.
        _applied, stop = _stage_walk(stages, owned_below, total_below,
                                     ctx.tentative_kept == kept_before)
        handed = _truncate_stages(stages, stop)
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
    pushed_held = ctx.tentative_kept == kept_before
    # The join below, if it applied some of these conjuncts, says which ones
    # (by identity) every row that came up has passed; those are skipped here
    # unless a row was kept on an error below, since such a row must reach
    # the FILTER untouched and cannot be told apart from the others.
    below = ctx.join_prefilter_report
    ctx.join_prefilter_report = None
    applied_below = below[0] if (below is not None and not below[1]) else set()
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
    project = make_join_projector(b1, b2, null_right)
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
    if prefilter is not None:
        binders = [binder for binder, _conjuncts in stages]
        applied_ids = set()
        errored = [False]
        self_names = {b1.upper(), '_1'}
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
            def owned_here(fields):
                # A joined row carries a left element's field exactly as the
                # element does whenever no right element has the name (§7.4,
                # pair by pair) -- and no row depends on another, so a drop
                # below changes nothing above but the rows it drops.
                return not (fields & right_side.keys) and not (fields & above_keys)
            def total_here(reqs):
                return _totality(reqs, left_side, right_side, above)
            applied, _stop = _stage_walk(stages, owned_here, total_here, pushed_held)
            for conjunct, fields, binder in applied:
                applied_ids.add(id(conjunct))
                if id(conjunct) in applied_below:
                    continue
                if fields & self_names and not (fields & left_side.first):
                    conjunct = _read_self(conjunct, self_names, binder)
                prefix.append(conjunct)
        def rejects(row, frame):
            # The left loop's frame, every stage's binder naming the row.
            for binder in binders:
                frame[binder] = row
            for conjunct in prefix:
                try:
                    keep = args.eval_node(conjunct).as_bool(conjunct.pos)
                except SelError:
                    errored[0] = True
                    return False
                if not keep:
                    return True
            return False

    if equi is not None and sample_right is not None:
        left_expr, right_expr, numeric = equi
        buckets = {}
        frame_right = {b2: None, b2.lower(): None, '_2': None}
        ctx.push_frame(frame_right)
        try:
            for item in iter_collection_items(right_value):
                row = ensure_row_table_alias(item, b2) if needs_right_alias else item
                frame_right[b2] = row
                frame_right[b2.lower()] = row
                frame_right['_2'] = row
                key = canonical_join_key(args.eval_node(right_expr), numeric)
                if key is not None:
                    buckets.setdefault(key, []).append(row)
                if gather is not None:
                    gather.add(row)
        finally:
            ctx.pop_frame()
        if prefilter is not None:
            if gather is not None:
                right_side = _SideFacts(right_value, gather.keys | {b.upper() for b in b2_names}, left_join,
                                        b2_names)
            settle_prefix()

        # A FILTER keeps its input's keys, so the rows dropped here still count
        # towards the numbering of the rows kept: the join key is computed
        # first, as it is for every row (and raises where it would have), the
        # matches say how many joined rows the dropped row stood for, and the
        # kept rows are emitted under the positions they would have had.
        keys = [] if (prefix and not deep) else None
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
                and left_expr.obj.name.upper() in (b1.upper(), '_1', '_')):
            fast_field = left_expr.idx.v
        frame_left = {b1: None, b1.lower(): None, '_1': None, '_': None}
        if prefilter is not None:
            for binder in binders:
                frame_left.setdefault(binder, None)
        ctx.push_frame(frame_left)
        try:
            for item in iter_collection_items(left_value):
                row = ensure_row_table_alias(item, b1) if needs_left_alias else item
                asked = False
                if fast_field is not None and row.get(fast_field) is not None:
                    asked = True
                    if rejects(row, frame_left):
                        continue
                frame_left[b1] = row
                frame_left[b1.lower()] = row
                frame_left['_1'] = row
                frame_left['_'] = row
                key = canonical_join_key(args.eval_node(left_expr), numeric)
                matches = buckets.get(key) if key is not None else None
                if prefix and not asked and rejects(row, frame_left):
                    if not deep:
                        position += len(matches) if matches else (1 if left_join else 0)
                    continue
                if matches:
                    for right in matches:
                        output.append(project(row, right))
                        if keys is not None:
                            keys.append(str(position))
                        position += 1
                elif left_join:
                    output.append(project(row, None))
                    if keys is not None:
                        keys.append(str(position))
                    position += 1
        finally:
            ctx.pop_frame()
        if prefilter is not None:
            ctx.join_prefilter_report = (applied_ids, errored[0])
        if keys is not None and len(keys) != position - 1:
            return Value.list(output, keys)
    else:
        # No pre-filter on the general join: the numbering of the kept rows
        # would need the count of matches of every dropped row, which is the
        # predicate scan the pre-filter exists to avoid.
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
                    right = ensure_row_table_alias(right_item, b2) if needs_right_alias else right_item
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
       fn=lambda args, ctx: _link(args, ctx, False))
define('LINK_LEFT', 3, 5, lazy=True, binds=True,
       fn=lambda args, ctx: _link(args, ctx, True))
