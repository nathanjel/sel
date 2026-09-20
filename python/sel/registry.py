"""The function table. Fixed at startup — SEL has no DEFUN — which is what lets
unknown names and wrong argument counts be caught at compile time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ._builtin_manifest import BUILTIN_MANIFEST
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
    max = min if max is None else max   # noqa: A001
    # The shipped table is authored once, in spec/builtins.json, and rendered
    # into _builtin_manifest.py. A name the manifest knows is held to it:
    # min/max/lazy/binds must agree, and the extra arity rule (COND's odd
    # count, LINK's three-or-five) comes from the manifest rather than from
    # here — one body for all five hosts. A name it does not know is a host's
    # own function (examples/fn-*) and passes.
    m = BUILTIN_MANIFEST.get(key)
    if m is not None:
        m_min, m_max, m_lazy, m_binds, rule = m
        wrong = []
        if min != m_min:
            wrong.append(f'min {min} vs {m_min}')
        if max != m_max:
            wrong.append(f'max {max} vs {m_max}')
        if lazy != m_lazy:
            wrong.append(f'lazy {lazy} vs {m_lazy}')
        if binds != m_binds:
            wrong.append(f'binds {binds} vs {m_binds}')
        if arity_error is not None:
            wrong.append('an arity rule of its own, which the manifest owns')
        if wrong:
            raise RuntimeError(f'SEL function {key} disagrees with spec/builtins.json: ' + '; '.join(wrong))
        if rule is not None:
            arity_error = _manifest_arity_error(rule)
    _table[key] = Spec(name=key, min=min, max=max,
                       lazy=lazy, binds=binds, arity_error=arity_error, fn=fn)


def _manifest_arity_error(rule):
    kind, detail, message = rule
    if kind == 'parity':
        odd = detail == 'odd'
        return lambda n: None if (n % 2 == 1) == odd else message.replace('{count}', str(n))
    allowed = frozenset(detail)
    return lambda n: None if n in allowed else message.replace('{count}', str(n))


def assert_manifest_covered() -> None:
    """Called once the shipped modules have registered: a manifest entry with
    no definition is a host that would silently lack a builtin the others have."""
    missing = [name for name in BUILTIN_MANIFEST if name not in _table]
    if missing:
        raise RuntimeError('spec/builtins.json names builtins this host never defined: ' + ', '.join(missing))


def lookup(name: str) -> Spec | None:
    return _table.get(ascii_upper(name))


def names() -> list[str]:
    return sorted(_table.keys())
