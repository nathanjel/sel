"""SEL errors. See spec/errors.md — codes are contract, messages are not."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn


@dataclass(frozen=True, slots=True)
class Pos:
    """A source position. 1-based line and column, counted in code points."""

    line: int = 0
    col: int = 0
    offset: int = 0


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


def fail(code: str, message: str, pos: Pos | None = None) -> NoReturn:
    """Raise at the innermost point of failure. Nothing wraps this on the way out."""
    raise SelError(code, message, pos)
