"""The function table. Fixed at startup — SEL has no DEFUN — which is what lets
unknown names and wrong argument counts be caught at compile time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .lexer import ascii_upper

INF = float('inf')


@dataclass(slots=True)
class Spec:
    name: str
    min: int
    max: float
    lazy: bool = False
    binds: bool = False          # introduces an element binder; see dependencies()
    # Optional extra arity rule, checked at compile time after min/max. Returns
    # a message when the count is wrong, or None when it is fine.
    arity_error: Callable[[int], str | None] | None = None
    fn: Callable[..., Any] | None = field(default=None)


_table: dict[str, Spec] = {}


def define(name: str, min: int, max: float | None = None, *,   # noqa: A002
           lazy: bool = False, binds: bool = False,
           arity_error: Callable[[int], str | None] | None = None,
           fn: Callable[..., Any]) -> None:
    # ASCII, not str.upper(): every caller passes an already-uppercased ASCII
    # identifier, but that is the caller's invariant and this is an API
    # boundary. str.upper() would fold "ß" to "SS" and change the name's length.
    key = ascii_upper(name)
    if key in _table:
        raise RuntimeError(f'SEL function {key} defined twice')
    _table[key] = Spec(name=key, min=min, max=min if max is None else max,
                       lazy=lazy, binds=binds, arity_error=arity_error, fn=fn)


def lookup(name: str) -> Spec | None:
    return _table.get(ascii_upper(name))


def names() -> list[str]:
    return sorted(_table.keys())
