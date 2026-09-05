"""Translator errors. See sql/errors.md -- codes are contract, messages are not.

Unlike SelError the message here is expected to be read: a translator error is
not a bug report, it is an answer. *This rule cannot be pushed into this
database, and here is what stopped it.*
"""

from __future__ import annotations

from typing import NoReturn

from ..errors import Pos


class SqlError(Exception):
    """One class, one message. No diagnostics object, no `explain()` API."""

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


def refuse(code: str, message: str, pos: Pos | None = None) -> NoReturn:
    """Raise at the point of failure. Nothing wraps this on the way out, the same
    rule spec/errors.md sets for the evaluator.
    """
    raise SqlError(code, message, pos)
