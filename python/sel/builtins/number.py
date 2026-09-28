from typing import Any

from .. import decimal as D
from ..errors import fail
from ..registry import INF, define
from ..value import Value

# spec/SPEC.md §6.4. Without these, a size argument nobody meant to write takes
# down the host instead of failing as a rule error.
MAX_SCALE = 1000000
MAX_POWER = 100000


def check_sized_int(d: D.Dec, name: str, arg_num: int, limit: int, what: str, pos: Any) -> int:
    if not D.is_integer(d):
        fail('E_NOT_INT', f'{name} argument {arg_num} must be a whole number', pos)
    n = D.to_safe_int(d)
    if n < 0:
        fail('E_RANGE', f'{name} argument {arg_num} must not be negative', pos)
    if n > limit:
        fail('E_RANGE', f'{what} {n} exceeds the maximum of {limit}', pos)
    return n


define('ABS', 1, 1, fn=lambda a, ctx: Value.num(D.abs_(a.dec(0))))
define('SIGN', 1, 1, fn=lambda a, ctx: Value.int(D.sign(a.dec(0))))
define('CEIL', 1, 1, fn=lambda a, ctx: Value.num(D.ceil(a.dec(0))))
define('FLOOR', 1, 1, fn=lambda a, ctx: Value.num(D.floor(a.dec(0))))
define('TRUNC', 1, 1, fn=lambda a, ctx: Value.num(D.trunc(a.dec(0))))
define('CANON', 1, 1, fn=lambda a, ctx: Value.num(D.trim_scale(a.dec(0))))

define('ROUND', 2, 2,
       fn=lambda a, ctx: Value.num(D.round(a.dec(0), check_sized_int(a.dec(1), 'ROUND', 2, MAX_SCALE, 'ROUND scale', a.pos_of(1)), a.pos)))
define('POWER', 2, 2,
       fn=lambda a, ctx: Value.num(D.power(a.dec(0), check_sized_int(a.dec(1), 'POWER', 2, MAX_POWER, 'POWER exponent', a.pos_of(1)), a.pos)))


def _min(a, ctx):
    best = a.dec(0)
    for i in range(1, a.count()):
        d = a.dec(i)
        if D.cmp(d, best) < 0:
            best = d
    return Value.num(best)


def _max(a, ctx):
    best = a.dec(0)
    for i in range(1, a.count()):
        d = a.dec(i)
        if D.cmp(d, best) > 0:
            best = d
    return Value.num(best)


define('MIN', 1, INF, fn=_min)
define('MAX', 1, INF, fn=_max)

define('ISNUM', 1, 1, fn=lambda a, ctx: Value.bool(a.val(0).looks_numeric()))
