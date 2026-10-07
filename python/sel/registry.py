"""The function table. Fixed at startup — SEL has no DEFUN — which is what lets
unknown names and wrong argument counts be caught at compile time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ._builtin_manifest import BUILTIN_MANIFEST, BINDING_FORMS, INF, REGEX_CALLS  # noqa: F401 - INF is re-exported
from .lexer import ascii_upper


# spec/SPEC.md §8.1: an identifier that starts with a letter. Checked with
# fullmatch, because `$` also matches before a trailing newline.
_HOST_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*', re.ASCII)

# The regex builtins (spec §7.8) and the arguments each takes its pattern and
# its flags in, from the manifest (spec/builtins.json `regex`): the parser checks
# their literal patterns, the SQL translator rewrites them, and both find the
# positions here.
REGEX_PATTERN_AT = {name: pattern for name, (pattern, _flags) in REGEX_CALLS.items()}
REGEX_FLAG_AT = {name: flags for name, (_pattern, flags) in REGEX_CALLS.items()}


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
    # here — one body for every host. A name it does not know is a host's
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


# The sort builtins, by whether the last argument is a count (the TOP family).
_SORTS = {'SORT': False, 'SORT_DESC': False, 'SORT_BY': False,
          'TOP': True, 'TOP_DESC': True, 'TOP_BY': True}
_SORT_FORMS: dict[tuple, tuple] = {}


def sort_form(name: str, args) -> tuple:
    """(binder, key, direction): the argument index of each in a SORT, SORT_DESC,
    SORT_BY, TOP, TOP_DESC or TOP_BY call, or None where the form has none --
    no key (SORT(list), TOP(list, n)), no binder, or no direction argument (a
    forced or default one). Read off the manifest's binding forms through
    binding_form, so the evaluator, the optimiser and the translator decode
    the forms one way; the TOP family's count, always last, is none of the
    three. A form's choice depends only on the count and on two shapes --
    whether argument 2 is a text literal, whether argument 1 is a bare name
    (spec §7.3: a text-literal direction wins over a bare name) -- so the
    answer is memoised on those."""
    n = len(args)
    k = (name, n, n > 2 and args[2].t == 'text',
         n > 1 and args[1].t == 'var' and not args[1].grouped)
    form = _SORT_FORMS.get(k)
    if form is None:
        form = _SORT_FORMS[k] = _decode_sort_form(name, args)
    return form


def _decode_sort_form(name: str, args) -> tuple:
    found = binding_form(name, args)
    if found is None:                    # a count no form takes: the parser refused it
        raise ValueError(f'{name} has no form for {len(args)} arguments')
    scopes = found[0]
    if _SORTS[name]:
        scopes = scopes[:-1]
    binder = scopes.index('binder') if 'binder' in scopes else None
    key = scopes.index('inner') if 'inner' in scopes else None
    direction = next((i for i in range(key + 1, len(scopes)) if scopes[i] == 'outer'), None) \
        if key is not None else None
    return binder, key, direction


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


def may_have_effects(name: str) -> bool:
    """The effects classification every analysis asks (spec/SPEC.md §8.1):
    whether a call to NAME may keep, read or change values beyond its result,
    so that no copy may be left out around it and nothing may be evaluated out
    of order across it. Only a shipped builtin -- a name the manifest knows,
    defined by the library itself (define() refuses a second definition, and
    register_function() a builtin's name) -- is assumed not to. Every other
    function is the application's, however it was installed: register_function,
    or a define() outside the manifest, strict, lazy or binding. Defining a
    function below the public API is not a declaration that it is pure.

    Registration is a separate question (host_arity, register_function's
    replacement rule), answered by _host alone."""
    key = ascii_upper(name)
    return key in _host or key not in BUILTIN_MANIFEST


def host_arity(name: str) -> tuple[int, int] | None:
    """The [min, max] of a host function registered with register_function(),
    or None when the name is not one. The SQL layer reads it: a host function's
    SQL spelling is checked against, and recorded with, this arity."""
    key = ascii_upper(name)
    if key not in _host:
        return None
    spec = _table[key]
    return spec.min, int(spec.max)


def lookup(name: str) -> Spec | None:
    return _table.get(ascii_upper(name))


def names() -> list[str]:
    return sorted(_table.keys())
