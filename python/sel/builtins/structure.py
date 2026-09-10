from ..registry import INF, define
from ..value import NONE, Value


def elements(value):
    if value.size() > 0:
        return value.entries()
    return [] if value.kind == NONE else [('1', value)]


define('COUNT', 1, 1, fn=lambda args, ctx: Value.int(args.val(0).size()))

define('INDEXES', 1, 1,
       fn=lambda args, ctx: Value.list([Value.text(k) for k in args.val(0).keys()]))

define('HAS', 2, 2, fn=lambda args, ctx: Value.bool(args.val(0).has(args.text(1))))

define('LIST', 0, INF,
       fn=lambda args, ctx: Value.list([args.val(i).clone() for i in range(args.count())]))


def _record(args, ctx):
    rec = Value.none()
    n = args.count()
    for i in range(0, n, 2):
        rec.set(args.text(i), args.val(i + 1).clone())
    return rec


define('RECORD', 0, INF,
       arity_error=lambda count: f"RECORD takes an even number of arguments (key-value pairs), got {count}" if count % 2 != 0 else None,
       fn=_record)


def _take(args, ctx):
    val = args.val(0)
    count = args.non_neg_int(1)
    if count == 0 or val.is_null():
        return Value.list([])
    ents = elements(val)
    return Value.list([item.clone() for _, item in ents[:count]])


define('TAKE', 2, 2, fn=_take)


def _drop(args, ctx):
    val = args.val(0)
    count = args.non_neg_int(1)
    if val.is_null():
        return Value.list([])
    ents = elements(val)
    if count >= len(ents):
        return Value.list([])
    return Value.list([item.clone() for _, item in ents[count:]])


define('DROP', 2, 2, fn=_drop)


def _select_cols(args, ctx):
    val = args.val(0)
    if val.is_null():
        return Value.list([])
    col_count = args.count()
    cols = [args.text(i) for i in range(1, col_count)]
    ents = elements(val)
    out = []
    for _, row in ents:
        new_row = Value.none()
        for c in cols:
            if row.has(c):
                new_row.set(c, row.get(c).clone())
        out.append(new_row)
    return Value.list(out)


define('SELECT_COLS', 2, INF, fn=_select_cols)


def _distinct(args, ctx):
    val = args.val(0)
    if val.is_null():
        return Value.list([])
    ents = elements(val)
    out = []
    for _, item in ents:
        if not any(item.eql(existing) for existing in out):
            out.append(item.clone())
    return Value.list(out)


define('DISTINCT', 1, 1, fn=_distinct)

