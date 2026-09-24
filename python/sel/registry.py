"""The function table. Fixed at startup — SEL has no DEFUN — which is what lets
unknown names and wrong argument counts be caught at compile time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ._builtin_manifest import BUILTIN_MANIFEST, BINDING_FORMS
from .lexer import ascii_upper

INF = float('inf')

# spec/SPEC.md §8.1: an identifier that starts with a letter. Checked with
# fullmatch, because `$` also matches before a trailing newline.
_HOST_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*', re.ASCII)


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


# A host's own binding function (define(..., binds=True) outside the manifest,
# examples/fn-complex) has no manifest forms; it gets the two classic shapes.
_GENERIC_FORMS = (
    (('outer', 'inner'), None, ('_', '_K')),
    (('outer', 'binder', 'inner'), (1, 'name'), ('_K',)),
)


def binding_form(name: str, args, spec=None):
    """Which argument of a binding call runs where (spec/builtins.md, "Binding
    forms"): a tuple of per-argument scopes -- 'outer' (evaluated where the
    call is), 'binder' (a bare name, never evaluated) or 'inner' (once per
    element) -- and the names bound inside. None when the call is not a
    binding builtin or no form takes this count: the evaluator would refuse
    it, and a static consumer reads every argument where the call stands. The
    dependency walker and the SQL layer's stage 1 both classify through here,
    so they cannot disagree."""
    key = ascii_upper(name)
    forms = BINDING_FORMS.get(key)
    if forms is None:
        if spec is None:
            spec = _table.get(key)
        if spec is None or not spec.binds:
            return None
        forms = _GENERIC_FORMS
    for scopes, when, binds in forms:
        if len(scopes) != len(args):
            continue
        if when is not None:
            a = args[when[0]]
            ok = (a.t == 'var' and not a.grouped) if when[1] == 'name' else a.t == 'text'
            if not ok:
                continue
        bound = list(binds)
        for i, scope in enumerate(scopes):
            if scope == 'binder' and args[i].t == 'var':
                bound.append(args[i].name)
        return scopes, bound
    return None


def assert_manifest_covered() -> None:
    """Called once the shipped modules have registered: a manifest entry with
    no definition is a host that would silently lack a builtin the others have."""
    missing = [name for name in BUILTIN_MANIFEST if name not in _table]
    if missing:
        raise RuntimeError('spec/builtins.json names builtins this host never defined: ' + ', '.join(missing))


# Names registered through register_function(), which alone may be replaced.
_host: set[str] = set()


def register_function(name: str, min: int, max: int,   # noqa: A002
                      fn: Callable[[Any], Any]) -> None:
    """An application's own strict function (spec/SPEC.md §8.1).

    It adds to the language and never changes it: a builtin's name or a
    reserved word is refused, and re-registering a host function replaces it.
    A bad registration is a programming error, so it raises ValueError or
    TypeError rather than SelError.
    """
    from .lexer import RESERVED
    from .value import Value
    if not isinstance(name, str) or not _HOST_NAME.fullmatch(name):
        raise ValueError(f'SEL function name must be ASCII letters, digits and _, '
                         f'starting with a letter: {name!r}')
    key = ascii_upper(name)
    if key in RESERVED:
        raise ValueError(f'{key} is a reserved word')
    if key in _table and key not in _host:
        raise ValueError(f'{key} is a builtin; a host function cannot replace it')
    if (type(min) is not int or type(max) is not int  # noqa: E721 - bool is not an arity
            or min < 0 or max < min):
        raise ValueError(f'SEL function {key}: arity must be whole numbers with 0 <= min <= max')
    if not callable(fn):
        raise TypeError(f'SEL function {key}: fn is not callable')

    def call(args, ctx):   # noqa: ARG001 - a host function sees the arguments only
        result = fn(args)
        if not isinstance(result, Value):
            raise TypeError(f'SEL function {key} returned {type(result).__name__}, not a Value')
        return result

    _table[key] = Spec(name=key, min=min, max=max, fn=call)
    _host.add(key)


def lookup(name: str) -> Spec | None:
    return _table.get(ascii_upper(name))


def names() -> list[str]:
    return sorted(_table.keys())
