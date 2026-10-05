"""The size caps of SPEC 6.4 (MAX_TEXT_LEN, MAX_COLLECTION): the length a result
WOULD have is worked out from the operands' lengths and refused before anything
is built, at the node that builds it. Python integers do not overflow, so a count
of 10**400 is simply a length that is over the cap -- but only when the result is
not empty: an empty result is never too large (`REPEAT("", 10**30)` is `""`)."""

from typing import Any

from . import decimal as D
from ._limits import MAX_COLLECTION, MAX_POWER_EXPONENT, MAX_ROUND_SCALE, MAX_TEXT_LEN
from .errors import fail


def check_text(length: int, pos, what: str = 'result') -> None:
    if length > MAX_TEXT_LEN:
        fail('E_RANGE', f'{what} would be longer than {MAX_TEXT_LEN}', pos)


def check_collection(count: int, pos, what: str = 'result') -> None:
    if count > MAX_COLLECTION:
        fail('E_RANGE', f'{what} would have more than {MAX_COLLECTION} children', pos)


# The size arguments of ROUND (a scale, MAX_ROUND_SCALE) and POWER (an exponent,
# MAX_POWER_EXPONENT), spec/SPEC.md §6.4: without a cap, a size argument nobody
# meant to write takes down the host instead of failing as a rule error. Checked
# by the builtins and by the evaluator's math plan alike, which is why it lives
# here and not with the builtins.
def check_sized_int(d: D.Dec, name: str, arg_num: int, limit: int, what: str, pos: Any) -> int:
    if not D.is_integer(d):
        fail('E_NOT_INT', f'{name} argument {arg_num} must be a whole number', pos)
    n = D.to_safe_int(d)
    if n < 0:
        fail('E_RANGE', f'{name} argument {arg_num} must not be negative', pos)
    if n > limit:
        # The number itself only when it is short: str() of a million-digit argument is
        # work the refusal does not need, and the message is not contract.
        shown = str(n) if n.bit_length() <= 128 else f'of {n.bit_length()} bits'
        fail('E_RANGE', f'{what} {shown} exceeds the maximum of {limit}', pos)
    return n
