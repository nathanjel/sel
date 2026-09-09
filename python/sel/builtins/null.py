from ..registry import INF, define
from ..value import Value


def _is_null(args, ctx):
    return Value.bool(args.val(0).is_null())


def _is_not_null(args, ctx):
    return Value.bool(not args.val(0).is_null())


def _coalesce(args, ctx):
    for i in range(args.count()):
        v = args.val(i)
        if not v.is_null():
            return v
    return Value.null()


def _get(args, ctx):
    target = args.val(0)
    key = args.text(1)
    if not target.is_null() and target.has(key):
        val = target.get(key)
        if val is not None:
            return val
    if args.count() > 2:
        return args.val(2)
    return Value.null()


def _path(args, ctx):
    target = args.val(0)
    path_str = args.text(1)
    if path_str == '':
        return target
    segments = path_str.split('.')
    cur = target
    for seg in segments:
        if cur.is_null() or not cur.has(seg):
            if args.count() > 2:
                return args.val(2)
            return Value.null()
        cur = cur.get(seg)
        if cur is None:
            if args.count() > 2:
                return args.val(2)
            return Value.null()
    return cur


def _is_blank(args, ctx):
    return Value.bool(args.val(0).is_vacuous())


def _is_present(args, ctx):
    return Value.bool(not args.val(0).is_vacuous())


define('IS_NULL', 1, 1, fn=_is_null)
define('IS_NOT_NULL', 1, 1, fn=_is_not_null)
define('COALESCE', 1, INF, lazy=True, fn=_coalesce)
define('GET', 2, 3, lazy=True, fn=_get)
define('PATH', 2, 3, lazy=True, fn=_path)
define('IS_BLANK', 1, 1, fn=_is_blank)
define('IS_PRESENT', 1, 1, fn=_is_present)
