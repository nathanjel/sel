from ..errors import fail
from ..registry import INF, define
from ..value import Value


# The whole of SEL's control flow. Lazy, so only the taken branch is evaluated —
# exactly the property the AST calling convention exists to provide.
def _if(args, ctx):
    if args.bool(0):
        return args.val(1)
    if args.count() == 3:
        return args.val(2)
    return Value.text('')


define('IF', 2, 3, lazy=True, fn=_if)


# Flat multi-branch selection — sugar for a nested IF ladder, with exactly the
# same laziness: conditions are evaluated in order, and only the result that
# matches is evaluated at all.
#
# The argument count must be odd: condition/result pairs plus a mandatory
# default. IF can safely let its two-argument form default to "" because there
# is one branch and nothing to mis-pair, but with an even count here a single
# miscounted comma would shift every pair by one and still compile. Requiring
# the default turns that into a compile-time E_ARITY instead of a wrong answer.
def _cond(args, ctx):
    last = args.count() - 1
    for i in range(0, last, 2):
        if args.bool(i):
            return args.val(i + 1)
    return args.val(last)


define('COND', 3, INF, lazy=True, fn=_cond,
       arity_error=lambda n: (
           f'COND takes condition/result pairs and a final default '
           f'(an odd number of arguments), got {n}') if n % 2 == 0 else None)


# The one error a rule author raises deliberately.
define('ABORT', 1, 1, fn=lambda args, ctx: fail('E_ABORT', args.text(0), args.pos_of(0)))
