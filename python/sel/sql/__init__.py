"""SEL -> SQL translation.

The design is in ``docs/internals/sql-translation.md``; the dialect map format is normative
in ``sql/MAP.md`` and the error codes in ``sql/errors.md``. ``_map.py`` is
generated from ``sql/dialects/*.json`` by ``tools/gen-sql-map.mjs`` and is the
same data every host consumes.

The contract this host is graded against is ``sql/cases/*.sqlt``, run by
``python/bin/sqlt``: the cases assert an exact string, so PHP and Python agreeing
on all cases is a measurement rather than an intention.
"""

from __future__ import annotations

from typing import Any

from . import map
from ._map import DIALECTS
from .binding import Binding
from .bindings import Bindings
from .errors import SqlError
from .fragment import Fragment
from .relational_plan import RelationalPlan
from .translator import Translator
from .hybrid import HybridPlan, execute_hybrid, plan_hybrid

__all__ = [
    'DIALECTS', 'Binding', 'Bindings', 'Fragment', 'HybridPlan', 'RelationalPlan',
    'Sql', 'SqlError', 'execute_hybrid', 'map', 'plan_hybrid',
]


class Sql:
    """The public interface of the SQL layer. See docs/internals/sql-translation.md §10."""

    @staticmethod
    def translate(program, dialect: str, bindings: dict[str, Any] | None = None,
                  options: dict[str, Any] | None = None) -> Fragment:
        """Translate a compiled program into a SQL expression for one dialect.

        Raises SqlError, whose message is written to be read. Use this when you
        want to know why a rule cannot be pushed down: during development, in a
        build-time audit of a rule set, or in a test.
        """
        b = bindings if isinstance(bindings, Bindings) else Bindings(bindings or {})
        t = Translator(dialect, b, options or {})
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
    def translate_statement(program, dialect: str, bindings: dict[str, Any] | None = None,
                            options: dict[str, Any] | None = None) -> Fragment:
        """Translate a relational pipeline program into a SQL statement fragment."""
        b = bindings if isinstance(bindings, Bindings) else Bindings(bindings or {})
        t = Translator(dialect, b, options or {})
        return t.translate_statement(program.ast)

    @staticmethod
    def try_translate_statement(program, dialect: str, bindings: dict[str, Any] | None = None,
                                options: dict[str, Any] | None = None) -> Fragment | None:
        """The same, returning None instead of raising."""
        try:
            return Sql.translate_statement(program, dialect, bindings, options)
        except SqlError:
            return None

    @staticmethod
    def plan_hybrid(program, dialect: str,
                    bindings: dict[str, Any] | Bindings | None = None,
                    options: dict[str, Any] | None = None) -> HybridPlan:
        """Plan the maximal SQL prefix and an optional SEL continuation."""
        return plan_hybrid(program, dialect, bindings, options)

    @staticmethod
    def execute_hybrid(plan: HybridPlan, db_runner, context: Any = None):
        """Execute a pure SQL, pure memory, or split plan."""
        return execute_hybrid(plan, db_runner, context)

    # Cross-host spelling aliases.
    planHybrid = plan_hybrid
    executeHybrid = execute_hybrid

    @staticmethod
    def dialects() -> list[str]:
        """Every dialect that may be named in a translate() call."""
        return map.targets()
