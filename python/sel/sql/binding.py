"""How an application says where a SEL variable lives in the schema.

Constructed in code, never decoded from a document. That is the whole point:
this layer used to take a nested dict shaped like JSON and validate it by hand,
and a cross-host review found the two hosts disagreeing about what a malformed
one meant -- ``from: ["order_items"]`` was refused by PHP and spliced into an
identifier here; ``items`` as an object was accepted by one and refused by the
other. None of that was a decision anybody made; it was ``json_decode``'s shape
rules on one side and Python's on the other, leaking into the translator.

A typed constructor makes the whole class unrepresentable rather than refusable.
An application whose bindings come from a schema file generates these calls; SEL
parses nothing.

**The checks are in the bodies rather than in the annotations, deliberately.**
PHP would enforce a ``string`` parameter and refuse a list with a TypeError;
Python's annotations enforce nothing at run time, JS has no types to declare, and
Lisp's are advisory. A guarantee written as a signature is a guarantee three of
the six hosts do not make. Written in the body it is the same refusal, with the
same code, everywhere -- and ``SqlError`` is the class an application catches,
where a ``TypeError`` is not.

See docs/SQL-TRANSLATION.md §5.
"""

from __future__ import annotations

from typing import Any

from .. import decimal as D
from ..lexer import ascii_upper
from ..value import Value, quote_dump
from .errors import SqlError
from .fragment import KINDS as FRAGMENT_KINDS


class Binding:
    """A validated, normalised binding record.

    ``spec`` is what the translator reads. Nothing outside this module builds
    one, and the factories below are the only way in.
    """

    __slots__ = ('spec',)

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec

    # --- the four kinds ------------------------------------------------------

    @staticmethod
    def column(column: Any, table: Any = None, type: Any = 'UNKNOWN') -> 'Binding':  # noqa: A002
        """One column, optionally qualified by a table, optionally typed.

        ``type`` is what the kind guards read. Leaving it UNKNOWN is honest and
        costs the guards: an UNKNOWN operand passes every check, because the
        binding did not say and nothing here can either.
        """
        _check_name('column', column)
        if table is not None:
            _check_name('table', table)
        _check_type(type)
        return Binding({'kind': 'column', 'column': column,
                        'table': table, 'type': type})

    @staticmethod
    def raw(sql: Any, type: Any = 'UNKNOWN') -> 'Binding':  # noqa: A002
        """A column expressed as SQL this layer will not read.

        The one place an application writes SQL here. It is emitted verbatim, so
        whatever it contains is the application's promise rather than this
        layer's -- which is exactly why it is a named constructor and not a key
        somebody can leave in a map by accident.
        """
        _check_string('a raw column binding', sql)
        if sql == '':
            raise SqlError('E_SQL_BINDING', 'a raw column binding cannot be empty')
        _check_type(type)
        return Binding({'kind': 'column', 'raw': sql, 'type': type})

    @staticmethod
    def columns(*items: 'Binding') -> 'Binding':
        """An ordered set of columns, iterated by an aggregate and indexed by
        position: the first is ``V[1]``.
        """
        if not items:
            raise SqlError('E_SQL_BINDING',
                           'a columns binding needs at least one column')
        out = []
        for i, item in enumerate(items):
            if not isinstance(item, Binding) or item.spec.get('kind') != 'column':
                kind = item.spec.get('kind') if isinstance(item, Binding) else type(item).__name__
                raise SqlError('E_SQL_BINDING',
                               'a columns binding takes column bindings, and item '
                               f'{i + 1} is a {kind}')
            out.append(item.spec)
        return Binding({'kind': 'columns', 'items': out})

    @staticmethod
    def relation(from_: Any, alias: Any = None, fields: Any = None,
                 scalar: Any = None, correlate: Any = None) -> 'Binding':
        """A set of rows, rendered as a correlated subquery.

        ``fields`` maps a SEL key to a column binding; the keys are upper-cased
        here, once, so every consumer looks one up the same way. ``scalar`` names
        the field a bare reference means, and only a ONE-field relation may
        declare it -- a wider row is a map in SEL, and a map is not the value of
        one of its fields.

        ``correlate`` is SQL, like ``raw()``, and joins the subquery back to the
        outer row. Without it the subquery is over the whole table, which is
        legal and occasionally what you want.
        """
        _check_name('from', from_)
        if alias is not None:
            _check_name('alias', alias)
        return _make_relation({'kind': 'relation', 'from': from_},
                              alias, fields, scalar, correlate)

    @staticmethod
    def relation_query(query: Any, alias: Any = None, fields: Any = None,
                       scalar: Any = None, correlate: Any = None) -> 'Binding':
        """The same, over a query the application writes rather than a table."""
        _check_string('a relation query', query)
        if query == '':
            raise SqlError('E_SQL_BINDING', 'a relation query cannot be empty')
        if alias is not None:
            _check_name('alias', alias)
        return _make_relation({'kind': 'relation', 'from': {'raw': query}},
                              alias, fields, scalar, correlate)

    @staticmethod
    def value(v: Value, type: Any = None) -> 'Binding':  # noqa: A002
        """A constant the application supplies, inlined as a literal.

        Takes a Value, never a native number or string, and that is the fix for
        the last cross-host divergence here: PHP's ``json_decode`` turns a
        20-digit integer into a float, this host keeps it exact, and JS cannot
        tell ``1.0`` from ``1``. Asking the caller for a Value moves the decision
        to the line that knows the answer.

        ``type`` is NUM or nothing. It decides whether the value is emitted
        quoted, which is a question no inspection can settle: SEL numbers ARE
        text values (spec §4), so ``Value.num('5.00')`` and ``Value.text('5.00')``
        are one object.
        """
        if not isinstance(v, Value):
            raise SqlError('E_SQL_BINDING',
                           'a value binding takes a Value, and this is '
                           f'{type_name(v)}; build one with Value.num(), .text(), '
                           '.bool(), .bin() or .list()')
        if type is not None and type != 'NUM':
            _check_type(type)
        if type == 'NUM':
            _check_numeric('this value binding', v)
        return Binding({'kind': 'value', 'type': type, 'value': v})


def type_name(v: Any) -> str:
    return type(v).__name__


# --- internals ---------------------------------------------------------------

def _make_relation(base: dict[str, Any], alias: Any, fields: Any,
                   scalar: Any, correlate: Any) -> Binding:
    if fields is None:
        fields = {}
    if not isinstance(fields, dict):
        raise SqlError('E_SQL_BINDING',
                       'the fields of a relation binding must be a map of name to '
                       f'column binding, and this is {type_name(fields)}')
    if scalar is not None:
        _check_string("a relation binding's scalar", scalar)
    if correlate is not None:
        _check_string("a relation binding's correlate", correlate)
    out: dict[str, Any] = {}
    for name, b in fields.items():
        if not isinstance(b, Binding) or b.spec.get('kind') != 'column':
            raise SqlError('E_SQL_BINDING',
                           f'the field {name} of a relation binding must be a '
                           'column binding')
        # ascii_upper, matching PHP's strtoupper: str.upper() would fold "ß" to
        # "SS" and change the key's length.
        out[ascii_upper(str(name))] = b.spec
    if scalar is not None and ascii_upper(scalar) not in out:
        raise SqlError('E_SQL_BINDING',
                       f'a relation binding names {scalar} as its scalar, which is '
                       'not one of its fields')
    spec = {**base, 'alias': alias, 'fields': out}
    if scalar is not None:
        spec['scalar'] = scalar
    if correlate is not None:
        spec['correlate'] = {'raw': correlate}
    return Binding(spec)


def _check_string(what: str, v: Any) -> None:
    if not isinstance(v, str):
        raise SqlError('E_SQL_BINDING',
                       f'{what} must be a string, and this is {type_name(v)}')


def _check_name(what: str, v: Any) -> None:
    """An identifier the application supplied has to survive being quoted.

    ``Emit.ident`` doubles the quote character and passes everything else
    through, which is right for every character but two. A NUL terminates the C
    string libpq and sqlite3 are handed, so ``a\\0b`` is malformed SQL on all four
    servers rather than a column nobody has. An empty name quotes to ``""``,
    which PostgreSQL rejects and the other three accept -- a divergence with no
    upside.
    """
    _check_string(f"a binding's {what}", v)
    if v == '':
        raise SqlError('E_SQL_BINDING', f'a binding has an empty {what} name')
    if '\0' in v:
        raise SqlError('E_SQL_BINDING',
                       f'a binding has a {what} name containing a NUL, which no '
                       'dialect can quote')


def _check_type(t: Any) -> None:
    if not isinstance(t, str) or t not in FRAGMENT_KINDS:
        raise SqlError('E_SQL_BINDING',
                       f'a binding has type {t}; use one of '
                       + ', '.join(FRAGMENT_KINDS))


def _check_numeric(where: str, v: Value) -> None:
    """Every scalar reachable from a NUM-typed value binding."""
    if v.size() > 0:
        for k, child in v.entries():
            _check_numeric(f'{where}["{k}"]', child)
        return
    if v.is_none():
        return
    if not v.is_text() or not v.looks_numeric():
        shown = ('TRUE' if v.as_bool() else 'FALSE') if v.is_bool() else v.as_text()
        raise SqlError('E_SQL_BINDING',
                       f'{where} declares type NUM, which asks for it to be emitted '
                       'unquoted, but ' + quote_dump(shown) + ' is not a number')
    # looks_numeric is broader than canonical, and emit._numeric_literal emits
    # decimal.format's output rather than the caller's characters -- correct for
    # the AST path, where the lexer has already canonicalised, and wrong here,
    # where the application supplied the string and the evaluator was handed that
    # same string. "007" translated to 7 while SEL kept "007".
    text = v.as_text()
    if D.format(D.parse(text)) != text:
        raise SqlError('E_SQL_BINDING',
                       f'{where} declares type NUM and is ' + quote_dump(text)
                       + ', which is not how SEL writes that number; a NUM binding '
                       'is emitted unquoted and must already be canonical, so pass '
                       'it as text or drop the leading zeros')
