"""What an aggregate binder names for the duration of one element.

Three shapes, matching the three iteration shapes of docs/SQL-TRANSLATION.md §7,
plus one that exists only to carry a refusal -- so that `_K` inside a relation
body fails saying rows have no key, rather than falling through to the bindings
map and being reported as an unbound variable.
"""

from __future__ import annotations

from typing import Any

NODE = 'node'       # an element of a static list: an AST node, re-entered
COLUMN = 'column'   # one column reference, from a `columns` binding
ROW = 'row'         # a row of a relation: fields resolve to that relation's columns
NONE = 'none'       # in scope, but using it is an error with this reason


class Binder:
    __slots__ = ('shape', 'payload', 'reason')

    # Mirrored as class attributes so `Binder.ROW` works the way `Value.BOOL`
    # does, which is the spelling every host reads the same.
    NODE = NODE
    COLUMN = COLUMN
    ROW = ROW
    NONE = NONE

    def __init__(self, shape: str, payload: Any, reason: str | None = None) -> None:
        self.shape = shape
        self.payload = payload
        self.reason = reason

    @staticmethod
    def node(node: Any) -> 'Binder':
        return Binder(NODE, node)

    @staticmethod
    def column(column: dict[str, Any]) -> 'Binder':
        return Binder(COLUMN, column)

    @staticmethod
    def row(relation: dict[str, Any]) -> 'Binder':
        return Binder(ROW, relation)

    @staticmethod
    def none(reason: str) -> 'Binder':
        return Binder(NONE, None, reason)
