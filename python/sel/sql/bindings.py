"""Where a SEL variable lives in the schema.

The host answers ``dependencies()`` with one of these per name; nothing is
inferred, and a name with no binding is E_SQL_UNBOUND rather than a guess at a
column.
"""

from __future__ import annotations

from typing import Any

from ..errors import Pos
from ..lexer import ascii_upper
from .binding import Binding
from .errors import SqlError, refuse

class Bindings:
    __slots__ = ('_map',)

    def __init__(self, bindings: dict[str, Binding]) -> None:
        """Name -> Binding, and nothing else.

        The dict-of-dicts this used to take was a JSON document in all but name,
        and validating one by hand is where the two hosts diverged: PHP's
        ``is_array`` cannot tell a JSON object from a JSON array, ``isinstance``
        can, and neither difference was a decision anybody made. A Binding is
        built by a typed constructor (see binding.py), so the malformed shapes
        are unrepresentable rather than refusable, and this class has nothing
        left to validate.
        """
        self._map: dict[str, dict[str, Any]] = {}
        for name, b in bindings.items():
            if not isinstance(b, Binding):
                raise SqlError('E_SQL_BINDING',
                               f'the binding for {name} is a {type(b).__name__}; '
                               'build one with Binding.column(), .columns(), '
                               '.relation(), .relation_query(), .raw() or .value()')
            # ascii_upper, not str.upper(): the latter folds "ß" to "SS" and
            # changes the name's length, where PHP's strtoupper is ASCII-only.
            self._map[ascii_upper(str(name))] = b.spec

    def has(self, name: str) -> bool:
        return ascii_upper(name) in self._map

    def get(self, name: str, pos: Pos | None = None) -> dict[str, Any]:
        key = ascii_upper(name)
        if key not in self._map:
            known = sorted(self._map)
            tail = ('; no bindings were given' if not known
                    else '; bound names are ' + ', '.join(known))
            refuse('E_SQL_UNBOUND',
                   f'{key} is read by this rule but no binding says where it lives'
                   + tail, pos)
        return self._map[key]

    def names(self) -> list[str]:
        return sorted(self._map)

    def check_aliases(self, pos: Pos | None = None) -> None:
        """A relation alias may name only one thing.

        Two relations sharing an alias in one expression would produce a subquery
        correlated to the wrong rows, and the host chose the aliases, so the host
        can fix them.
        """
        seen: dict[str, str] = {}
        for name, b in self._map.items():
            if b['kind'] != 'relation':
                continue
            alias = b.get('alias')
            # A raw `from` is a dict, and a non-string alias reached a dict key
            # in PHP and raised a TypeError -- not a SqlError, so try_translate()
            # did not catch it and a host using the refusal-tolerant API got a
            # fatal instead of None.
            if alias is not None and not isinstance(alias, str):
                refuse('E_SQL_BINDING',
                       f'the relation binding for {name} has an alias that is not '
                       'a string', pos)
            if alias is None:
                frm = b['from']
                alias = str(frm.get('raw', '')) if isinstance(frm, dict) else str(frm)
            if alias in seen:
                refuse('E_SQL_BINDING',
                       f'relations {seen[alias]} and {name} share the alias {alias}; '
                       'give each one its own', pos)
            seen[alias] = name
