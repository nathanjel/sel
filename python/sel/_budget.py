"""The size caps of SPEC 6.4 (MAX_TEXT_LEN, MAX_COLLECTION): the length a result
WOULD have is worked out from the operands' lengths and refused before anything
is built, at the node that builds it. Python integers do not overflow, so a count
of 10**400 is simply a length that is over the cap -- but only when the result is
not empty: an empty result is never too large (`REPEAT("", 10**30)` is `""`)."""

from ._limits import MAX_COLLECTION, MAX_TEXT_LEN
from .errors import fail


def check_text(length: int, pos, what: str = 'result') -> None:
    if length > MAX_TEXT_LEN:
        fail('E_RANGE', f'{what} would be longer than {MAX_TEXT_LEN}', pos)


def check_collection(count: int, pos, what: str = 'result') -> None:
    if count > MAX_COLLECTION:
        fail('E_RANGE', f'{what} would have more than {MAX_COLLECTION} children', pos)
