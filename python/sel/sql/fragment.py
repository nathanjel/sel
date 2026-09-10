"""The result of a translation: a part list, its bound values, and the static
kind it produces.
"""

from __future__ import annotations

from typing import Any

from ..value import Value
from . import emit as _emit
from .errors import refuse

KINDS = ('NUM', 'TEXT', 'BOOL', 'BIN', 'UNKNOWN', 'LIST', 'STATEMENT')


class Fragment:
    """A rendered SQL expression.

    ``parts`` alternates finished SQL and parameter slots -- a str is SQL, an int
    is the 1-based index of a value in ``params``. The renderer never
    concatenates a literal into a string, so ``inline`` and ``params`` output are
    two ways of joining one structure rather than two code paths. A part list
    cannot be confused about where a literal ends, whatever the literal contains,
    and that is the class of bug this shape exists to make unreachable.
    """

    __slots__ = ('parts', 'params', 'param_kinds', 'kind', 'dialect', 'caveats',
                 'exact', 'sargable', 'guard', 'prefilter', 'separate_prefilter')

    def __init__(self, parts: list[Any], kind: str, dialect: str,
                 params: list[Value] | None = None,
                 param_kinds: list[str] | None = None,
                 caveats: list[str] | None = None,
                 exact: bool = False,
                 sargable: bool = False,
                 guard: bool = False) -> None:
        self.parts = parts
        self.kind = kind
        self.dialect = dialect
        self.params = params if params is not None else []
        # The literal form of each slot: NUM, TEXT, BOOL or BIN, parallel to
        # params. Kept beside the values rather than derived from them because it
        # cannot be derived -- see emit.literal.
        self.param_kinds = param_kinds if param_kinds is not None else []
        self.caveats = caveats if caveats is not None else []
        self.exact = exact
        self.sargable = sargable
        self.guard = guard
        self.prefilter: Fragment | None = None
        self.separate_prefilter: bool = False

    def as_value(self, mode: str = 'inline') -> str:
        """Usable in a select list, GROUP BY, ORDER BY or HAVING. Any kind but
        LIST, which is not a SQL value at all.
        """
        if self.kind == 'LIST':
            refuse('E_SQL_SHAPE',
                   'this expression yields a list, and a SQL expression is a scalar')
        if self.kind == 'STATEMENT':
            refuse('E_SQL_SHAPE',
                   'this expression yields a statement, and a SQL expression is a scalar; use as_statement()')
        return self._join(mode)

    def as_statement(self, mode: str = 'inline') -> str:
        """Usable as a top-level SQL query statement."""
        if self.kind != 'STATEMENT':
            refuse('E_SQL_SHAPE',
                   f'expected STATEMENT fragment, got {self.kind}; use as_value() or as_condition()')
        return self._join(mode)

    def as_condition(self, mode: str = 'inline') -> str:
        """Usable as a condition. BOOL as it stands, and nothing else.

        A NUM or TEXT fragment is refused rather than accepted. Silently allowing
        ``WHERE o.total`` is how a database turns a validation rule into the
        truthiness test SEL spent its whole design avoiding.
        """
        if self.kind == 'BOOL':
            return self._join(mode)
        # UNKNOWN was wrapped in isTrue here rather than trusted, which folds
        # NULL to false but not a number: `1 IS TRUE` is TRUE on MariaDB, and SEL
        # raises E_NOT_BOOL for a number in a condition. Wrapping cannot fix
        # that, so an undeclared column is no longer a condition; declare the
        # binding BOOL. isTrue stays in the map -- the aggregate skeletons use it
        # on a body that is already known to be BOOL.
        refuse('E_SQL_SHAPE',
               f'a condition must be BOOL, and this expression is {self.kind}; '
               'SQL has no truthiness and neither does SEL')

    def bindings(self) -> list[Value]:
        """The bound values for ``params`` mode, in placeholder order.

        Derived from the part list rather than returned as stored, because the
        two orders are not the same. A slot is numbered when it is created, and
        the template decides where it lands: ``FIND(needle, hay)`` maps to
        ``INSTR({1}, {0})``, so the second slot created is the first one emitted.
        A positional ``?`` carries no number, so a driver binds the first value to
        the first placeholder -- which is right only if this walks the output.

        A slot appearing more than once yields its value more than once, which is
        also right: two placeholders need two bindings, even of the same value.
        """
        return [self.params[p - 1] for p in self.parts
                if not isinstance(p, str) and not self._is_inline(p)]

    def _is_inline(self, slot: int) -> bool:
        """True for a slot rendered as a literal in every mode, never as a
        parameter.

        Three forms qualify, for the same underlying reason: **none carries any
        character the caller chose**, so there is nothing for a placeholder to
        protect, and each is damaged by being sent as a string.

        NUM, because no coercion of a bound string reproduces a bare numeric
        literal. MariaDB reads ``2.50`` as DECIMAL with scale 2 and
        ``12345678901234567890.12345`` as DECIMAL with 25 digits; a parameter is
        untyped, and every way of giving it a type picks the wrong one.
        ``CAST(? AS DECIMAL(65,10))`` pads the scale, so ``TRIM(2.50)`` answered
        "2.5000000000". ``(? + 0)`` drops the scale and floats above seventeen
        digits. With neither, ``(? = ?)`` compares two strings and 2.50 = 2.5 is
        FALSE. After emit._numeric_literal the characters a NUM literal can
        contain are digits, one ``.`` and a leading ``-``, by construction.

        BOOL, because the token is ``map.lexical(dialect, 'true'|'false')`` -- it
        comes out of the dialect document, not out of a rule. Binding it as a
        string breaks SQLite outright: ``1 = '1'`` is **0** there, since INTEGER
        and TEXT are different storage classes and no affinity applies to a bare
        parameter, so ``TRUE XOR TRUE`` answered TRUE in params mode and FALSE
        inline. Found by the fuzz lane on sqlite's first run.

        BIN, because a BIN parameter is bytes and a driver sends them through the
        connection's text encoding: on PostgreSQL 130 of the 256 single-byte
        values then failed -- 129 as `22021 invalid byte sequence for encoding
        "UTF8"` and 0x00 silently -- while the same values inlined through
        ``binaryLiteral`` were correct on all four dialects, all 256. A host
        cannot work around it: bindings() hands back Values, and the cast
        wrapping the placeholder is what breaks it.

        Everything else is still bound.
        """
        kind = self.param_kinds[slot - 1] if slot - 1 < len(self.param_kinds) else 'TEXT'
        return kind in ('NUM', 'BOOL', 'BIN')

    def is_exact(self) -> bool:
        """True when nothing about this translation is inexact."""
        return self.caveats == []

    def _join(self, mode: str) -> str:
        out: list[str] = []
        nth = 0                          # position in bindings(), not slot id
        for p in self.parts:
            if isinstance(p, str):
                out.append(p)
                continue
            kind = self.param_kinds[p - 1] if p - 1 < len(self.param_kinds) else 'TEXT'
            if mode != 'inline' and self._is_inline(p):
                # Never a placeholder; see _is_inline(). It does not advance nth
                # either, because it emits no placeholder for a binding to land in.
                out.append(_emit.literal(self.dialect, self.params[p - 1], kind))
                continue
            nth += 1
            if mode == 'inline':
                out.append(_emit.literal(self.dialect, self.params[p - 1], kind))
            elif mode == 'params':
                # The ordinal a numbered placeholder carries -- PostgreSQL's $n --
                # must agree with bindings(), which walks the output. The slot id
                # would not: it is a creation number, and a reordering template
                # emits creation numbers out of order.
                out.append(_emit.placeholder(self.dialect, nth))
            elif mode == 'debug':
                out.append(f'~{nth}~')
            else:
                raise RuntimeError(
                    f'unknown render mode {mode}; use inline, params or debug')
        return ''.join(out)
