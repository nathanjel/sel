from .. import decimal as D
from ..errors import fail
from ..registry import INF, define
from ..value import Value

# spec/SPEC.md §6.4. Without these, a size argument nobody meant to write takes
# down the host instead of failing as a rule error.
MAX_SCALE = 1000000
MAX_POWER = 100000


def _sized(args, i, limit, what):
    n = args.non_neg_int(i)
    if n > limit:
        fail('E_RANGE', f'{what} {n} exceeds the maximum of {limit}', args.pos_of(i))
    return n


define('ABS', 1, 1, fn=lambda a, ctx: Value.num(D.abs_(a.dec(0))))
define('SIGN', 1, 1, fn=lambda a, ctx: Value.int(D.sign(a.dec(0))))
define('CEIL', 1, 1, fn=lambda a, ctx: Value.num(D.ceil(a.dec(0))))
define('FLOOR', 1, 1, fn=lambda a, ctx: Value.num(D.floor(a.dec(0))))
define('TRUNC', 1, 1, fn=lambda a, ctx: Value.num(D.trunc(a.dec(0))))

define('ROUND', 2, 2,
       fn=lambda a, ctx: Value.num(D.round(a.dec(0), _sized(a, 1, MAX_SCALE, 'ROUND scale'), a.pos)))
define('POWER', 2, 2,
       fn=lambda a, ctx: Value.num(D.power(a.dec(0), _sized(a, 1, MAX_POWER, 'POWER exponent'), a.pos)))


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
