"""SEL errors. See spec/errors.md — codes are contract, messages are not."""

from __future__ import annotations

from typing import NoReturn


class Pos:
    """A source position. 1-based line and column, counted in code points.
    Immutable by convention (a frozen slots dataclass until PY-P5: one is built
    per token, so its construction cost showed in the lexer)."""

    __slots__ = ('line', 'col', 'offset')

    def __init__(self, line: int = 0, col: int = 0, offset: int = 0) -> None:
        self.line = line
        self.col = col
        self.offset = offset

    def __eq__(self, other: object) -> bool:
        if other.__class__ is not Pos:
            return NotImplemented
        return (self.line == other.line and self.col == other.col
                and self.offset == other.offset)

    def __hash__(self) -> int:
        return hash((self.line, self.col, self.offset))

    def __repr__(self) -> str:
        return f'Pos(line={self.line!r}, col={self.col!r}, offset={self.offset!r})'


class SelError(Exception):
    """`code` is a stable identifier from spec/errors.md and part of the
    language's contract; `message` is human text, free to change and to be
    translated. Conformance tests assert on the code and the position only.
    """

    __slots__ = ('code', 'message', 'line', 'col', 'offset')

    def __init__(self, code: str, message: str, pos: Pos | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.line = pos.line if pos else 0
        self.col = pos.col if pos else 0
        self.offset = pos.offset if pos else 0

    def __str__(self) -> str:
        return f'{self.code} at {self.line}:{self.col}: {self.message}'

    def __reduce__(self):
        # Exception's default reduce replays `args`, which holds only the
        # message, so pickle, copy and deepcopy -- and a ProcessPoolExecutor
        # carrying the error to its parent -- failed on the three-argument
        # constructor. Rebuild from the fields instead.
        return (_rebuild, (self.code, self.message, self.line, self.col, self.offset))


def _rebuild(code: str, message: str, line: int, col: int, offset: int) -> 'SelError':
    return SelError(code, message, Pos(line, col, offset))


def fail(code: str, message: str, pos: Pos | None = None) -> NoReturn:
    """Raise at the innermost point of failure. Nothing wraps this on the way out."""
    raise SelError(code, message, pos)

# spec/SPEC.md §6.4's three caps, which are one number. The parser's nesting, the
# evaluator's, and a value's -- each is a recursion over a structure the input can
# grow without bound, and each finds this host's own stack instead of an error if
# it is not counted. It lives here, with fail(), because this module is the one
# every other imports and none imports back, and because the number and the
# E_DEPTH it raises are the same fact.
from . import _limits as _limits

MAX_DEPTH = _limits.MAX_DEPTH   # spec/limits.json, checked against the spec text
