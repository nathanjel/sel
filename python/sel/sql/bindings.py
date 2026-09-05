"""Where a SEL variable lives in the schema.

The host answers ``dependencies()`` with one of these per name; nothing is
inferred, and a name with no binding is E_SQL_UNBOUND rather than a guess at a
column.
"""

from __future__ import annotations

from typing import Any

from .. import decimal as D
from ..errors import Pos
from ..lexer import ascii_upper
from ..value import Value, quote_dump
from .errors import SqlError, refuse
from .fragment import KINDS as FRAGMENT_KINDS

KINDS = ('column', 'columns', 'relation', 'value')


class Bindings:
    __slots__ = ('_map',)

    def __init__(self, bindings: dict[str, Any]) -> None:
        self._map: dict[str, dict[str, Any]] = {}
        for name, b in bindings.items():
            # ascii_upper, not str.upper(), and for the reason registry.define
            # already records: str.upper() folds "ß" to "SS" and changes the
            # name's length, while PHP's strtoupper -- which every one of these
            # transcribes -- is ASCII-only. A binding named "straße" would be
            # stored under a key six characters long and looked up under one
            # seven characters long, and nothing would say so.
            key = ascii_upper(str(name))
            self._map[key] = _validate(key, b)

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


# --- validation --------------------------------------------------------------

def _validate(name: str, b: Any) -> dict[str, Any]:
    if not isinstance(b, dict) or b.get('kind') is None:
        raise SqlError('E_SQL_BINDING',
                       f'the binding for {name} has no kind; use one of '
                       + ', '.join(KINDS))
    kind = str(b['kind'])
    if kind not in KINDS:
        raise SqlError('E_SQL_BINDING',
                       f'the binding for {name} has kind {kind}; use one of '
                       + ', '.join(KINDS))

    if kind == 'column':
        _check_column(name, b)
        # PHP's `$b + ['type' => 'UNKNOWN']` is a DEFAULT -- left-wins, so the
        # host's own type survives. Python's `|` and `{**a, **b}` are right-wins,
        # so the default goes first and the host's dict second. Getting this
        # backwards overwrites every declared type with UNKNOWN, silently, and
        # the kind guards stop guarding.
        return {'type': 'UNKNOWN', **b}

    if kind == 'columns':
        items_in = b.get('items')
        if not isinstance(items_in, list) or items_in == []:
            raise SqlError('E_SQL_BINDING',
                           f'the columns binding for {name} needs a non-empty items list')
        items = []
        for i, item in enumerate(items_in):
            _check_column(f'{name}[{i + 1}]', item)
            items.append({'type': 'UNKNOWN', **item})
        return {'kind': 'columns', 'items': items}

    if kind == 'relation':
        if b.get('from') is None:
            raise SqlError('E_SQL_BINDING',
                           f'the relation binding for {name} needs a from')
        fields: dict[str, Any] = {}
        for f, spec in (b.get('fields') or {}).items():
            _check_column(f'{name}["{f}"]', spec)
            fields[ascii_upper(str(f))] = {'type': 'UNKNOWN', **spec}
        # `raw` is the one place a host writes SQL this layer cannot check the
        # meaning of. It can still check the SHAPE, and must: str() on a dict
        # yields something that is then spliced into a correlated subquery as if
        # it were a join condition.
        if b.get('correlate') is not None:
            c = b['correlate']
            if not isinstance(c, dict) or not isinstance(c.get('raw'), str):
                raise SqlError('E_SQL_BINDING',
                               f'the relation binding for {name} has a correlate that '
                               'is not ["raw" => string]; correlate is raw SQL and '
                               'there is nothing else it can be')
        frm = b['from']
        if isinstance(frm, dict) and not isinstance(frm.get('raw'), str):
            raise SqlError('E_SQL_BINDING',
                           f'the relation binding for {name} has a from that is an '
                           'array but not ["raw" => string]')
        if isinstance(frm, str):
            _check_name(f'the relation binding for {name}', 'from', frm)
        if isinstance(b.get('alias'), str):
            _check_name(f'the relation binding for {name}', 'alias', b['alias'])
        if b.get('scalar') is not None and ascii_upper(str(b['scalar'])) not in fields:
            raise SqlError('E_SQL_BINDING',
                           f"the relation binding for {name} names {b['scalar']} as its "
                           'scalar, which is not one of its fields')
        # A right-wins merge, deliberately: `fields` and `alias` here must
        # REPLACE what the host wrote, because they are the normalised forms.
        # PHP needs array_merge to say this; the union operator kept the host's
        # raw lowercase field keys and every consumer then failed to find them,
        # because they all look up upper-case.
        return {**b, 'fields': fields, 'alias': b.get('alias')}

    # value
    #
    # `.get(k) is not None`, not `k in b`, throughout this file and the
    # translator. PHP's isset() -- which every one of these transcribes -- is
    # FALSE for a key holding an explicit null, so {"kind":"value","value":null}
    # is "needs a value" there. Membership makes it true, and the binding then
    # reached Value.from_native(None) and was refused later with a different
    # code. `in` is right only where PHP wrote array_key_exists, which is
    # map.entry() and map.entries() and nowhere else -- and there the difference
    # is the point: a null entry is a WITHDRAWAL, which must be found.
    if b.get('value') is None:
        raise SqlError('E_SQL_BINDING', f'the value binding for {name} needs a value')
    v = b['value']
    type_ = b.get('type')
    if type_ is not None and type_ not in FRAGMENT_KINDS:
        raise SqlError('E_SQL_BINDING',
                       f'the value binding for {name} has type {type_}; use one of '
                       + ', '.join(FRAGMENT_KINDS))
    # `type` decides whether a scalar goes out quoted or bare, which is a
    # question no inspection of the value can answer: SEL numbers are TEXT
    # values. See emit.literal.
    val = v if isinstance(v, Value) else Value.from_native(v)
    # Declaring NUM asks for the value to be emitted unquoted, so it has to be a
    # number. Checked here, where the message can name the binding, and again in
    # emit.literal, which is the last place before the characters go out.
    if type_ == 'NUM':
        _check_numeric(name, val)
    return {'kind': 'value', 'type': type_, 'value': val}


def _check_numeric(name: str, v: Value) -> None:
    """Every scalar reachable from a NUM-typed value binding."""
    if v.size() > 0:
        for k, child in v.entries():
            _check_numeric(f'{name}["{k}"]', child)
        return
    if v.is_none():
        return
    if not v.is_text() or not v.looks_numeric():
        shown = ('TRUE' if v.as_bool() else 'FALSE') if v.is_bool() else v.as_text()
        raise SqlError('E_SQL_BINDING',
                       f'the value binding for {name} declares type NUM, which asks '
                       'for it to be emitted unquoted, but ' + quote_dump(shown)
                       + ' is not a number')
    # looks_numeric is broader than canonical, and emit._numeric_literal emits
    # decimal.format's output rather than the caller's characters -- correct for
    # the AST path, where the lexer has already canonicalised (`007` is the
    # number 7 by the time it is a node), and wrong here, where the host supplied
    # the string and the evaluator was handed that same string.
    #
    # So {"value": "007", "type": "NUM"} translated to `7` while SEL kept "007":
    # LEN was 1 against 3, TRIM was "7" against "007", and `N $== "007"` was
    # false against TRUE. Only the text-preserving contexts diverged, which is
    # why the corpus's single witness -- 2.50, which round-trips -- never saw it.
    # The non-round-tripping set is exactly a leading zero (007, 00.50, 000) and
    # a negative zero (-0).
    #
    # Refused rather than canonicalised: rewriting the value here would still
    # disagree with the evaluator, which never sees this code.
    text = v.as_text()
    if D.format(D.parse(text)) != text:
        raise SqlError('E_SQL_BINDING',
                       f'the value binding for {name} declares type NUM and is '
                       + quote_dump(text) + ', which is not how SEL writes that '
                       'number; a NUM binding is emitted unquoted and must already '
                       'be canonical, so pass it as text or drop the leading zeros')


def _check_column(where: str, c: Any) -> None:
    if not isinstance(c, dict):
        raise SqlError('E_SQL_BINDING', f'{where} must be an array')
    if c.get('column') is None and c.get('raw') is None:
        raise SqlError('E_SQL_BINDING', f'{where} needs a column or a raw')
    # The same audit `correlate` and a raw `from` got, applied to the three
    # fields that were left out of it. str() on a dict yields characters that are
    # a legal identifier: given a schema with a column of that name, the wrong
    # column is read and nothing says so.
    for k in ('column', 'raw', 'table'):
        if c.get(k) is not None and not isinstance(c[k], str):
            raise SqlError('E_SQL_BINDING', f'{where} has a {k} that is not a string')
    _check_name(where, 'column', c.get('column'))
    _check_name(where, 'table', c.get('table'))
    if c.get('type') is not None and c['type'] not in FRAGMENT_KINDS:
        raise SqlError('E_SQL_BINDING',
                       f"{where} has type {c['type']}; use one of "
                       + ', '.join(FRAGMENT_KINDS))


def _check_name(where: str, what: str, v: Any) -> None:
    """An identifier the host supplied has to survive being quoted.

    ``Emit.ident`` doubles the quote character and passes everything else
    through, which is right for every character but two. A NUL terminates the C
    string libpq and sqlite3 are handed, so ``a\\0b`` is malformed SQL on all four
    servers rather than a column nobody has. An empty name quotes to ``""``,
    which PostgreSQL rejects as a zero-length delimited identifier and the other
    three accept -- a divergence with no upside.

    Refused rather than escaped: no dialect has an escape for a NUL inside an
    identifier, and a column whose name contains one does not exist.
    """
    if not isinstance(v, str):
        return
    if v == '':
        raise SqlError('E_SQL_BINDING', f'{where} has an empty {what} name')
    if '\0' in v:
        raise SqlError('E_SQL_BINDING',
                       f'{where} has a {what} name containing a NUL, which no dialect '
                       'can quote')
