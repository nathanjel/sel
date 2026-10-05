from typing import Any

from .. import decimal as D
from ..errors import fail
from ..registry import INF, define
from ..value import Value
from .._budget import MAX_POWER_EXPONENT, MAX_ROUND_SCALE, check_sized_int


define('ABS', 1, 1, fn=lambda a, ctx: Value._num_owned(D.abs_(a.dec(0))))
define('SIGN', 1, 1, fn=lambda a, ctx: Value.int(D.sign(a.dec(0))))
define('CEIL', 1, 1, fn=lambda a, ctx: Value._num_owned(D.ceil(a.dec(0), a.pos)))
define('FLOOR', 1, 1, fn=lambda a, ctx: Value._num_owned(D.floor(a.dec(0), a.pos)))
define('TRUNC', 1, 1, fn=lambda a, ctx: Value._num_owned(D.trunc(a.dec(0))))
define('CANON', 1, 1, fn=lambda a, ctx: Value._num_owned(D.trim_scale(a.dec(0))))

define('ROUND', 2, 2,
       fn=lambda a, ctx: Value._num_owned(D.round(a.dec(0), check_sized_int(a.dec(1), 'ROUND', 2, MAX_ROUND_SCALE, 'ROUND scale', a.pos_of(1)), a.pos)))
define('POWER', 2, 2,
       fn=lambda a, ctx: Value._num_owned(D.power(a.dec(0), check_sized_int(a.dec(1), 'POWER', 2, MAX_POWER_EXPONENT, 'POWER exponent', a.pos_of(1)), a.pos)))


def _min(a, ctx):
    best = a.dec(0)
    for i in range(1, a.count()):
        d = a.dec(i)
        if D.cmp(d, best) < 0:
            best = d
    return Value._num_owned(best)


def _max(a, ctx):
    best = a.dec(0)
    for i in range(1, a.count()):
        d = a.dec(i)
        if D.cmp(d, best) > 0:
            best = d
    return Value._num_owned(best)


define('MIN', 1, INF, fn=_min)
define('MAX', 1, INF, fn=_max)

define('ISNUM', 1, 1, fn=lambda a, ctx: Value.bool(a.val(0).looks_numeric()))
