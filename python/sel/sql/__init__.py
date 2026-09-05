"""SEL -> SQL translation.

The design is in ``docs/SQL-TRANSLATION.md``; the dialect map format is normative
in ``sql/MAP.md`` and the error codes in ``sql/errors.md``. ``_map.py`` is
generated from ``sql/dialects/*.json`` by ``tools/gen-sql-map.mjs`` and is the
same data every host consumes.

The contract this host is graded against is ``sql/cases/*.sqlt``, run by
``python/bin/sqlt``: the cases assert an exact string, so PHP and Python agreeing
on all 339 is a measurement rather than an intention.
"""

from __future__ import annotations

from typing import Any

from . import map
from ._map import DIALECTS
from .bindings import Bindings
from .errors import SqlError
from .fragment import Fragment
from .translator import Translator

__all__ = ['DIALECTS', 'Bindings', 'Fragment', 'Sql', 'SqlError', 'map']


class Sql:
    """The public interface of the SQL layer. See docs/SQL-TRANSLATION.md §10."""

    @staticmethod
    def translate(program, dialect: str, bindings: dict[str, Any] | None = None,
                  options: dict[str, Any] | None = None) -> Fragment:
        """Translate a compiled program into a SQL expression for one dialect.

        Raises SqlError, whose message is written to be read. Use this when you
        want to know why a rule cannot be pushed down: during development, in a
        build-time audit of a rule set, or in a test.
        """
        t = Translator(dialect, Bindings(bindings or {}), options or {})
        return t.translate(program.ast)

    @staticmethod
    def try_translate(program, dialect: str, bindings: dict[str, Any] | None = None,
                      options: dict[str, Any] | None = None) -> Fragment | None:
        """The same, returning None instead of raising.

        Refusal is an expected, ordinary outcome -- "this rule cannot be pushed
        down, evaluate it here instead" -- and an expected outcome should not need
        a try/except to observe. Only SqlError is caught: a bug in the translator
        must not be swallowed by the path that exists to handle refusals.
        """
        try:
            return Sql.translate(program, dialect, bindings, options)
        except SqlError:
            return None

    @staticmethod
    def dialects() -> list[str]:
        """Every dialect that may be named in a translate() call."""
        return map.targets()
