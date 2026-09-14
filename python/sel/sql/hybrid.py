"""SQL-prefix planning with an in-memory SEL continuation.

The planner follows the Lisp reference implementation: normalize and logically
optimize a relational pipeline, try the complete pipeline first, then try a
safe mixed MAP fall-through, and finally choose the longest translatable prefix.

The contract every host's planner meets is in docs/SQL-TRANSLATION.md §12.1
and is pinned by sql/cases/25-hybrid-plans.sqlt: the planner looks at the tree
the translator will see, ``source_tables`` names PHYSICAL sources (a relation's
``from``, or a relation query's text verbatim), a program stage 1 refuses is a
pure-memory plan rather than an exception, and ``options`` is one dict that
reaches both the logical optimiser and the translator.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import Program, Value
from ..lexer import ascii_upper
from ..optimizer import build_pipeline, copy_node, unwind_pipeline
from ..parser import Node
from ..optimizer import optimize_ast_logical
from . import map as sqlmap
from . import constants as sql_constants
from . import normalise as sql_normalise
from .bindings import Bindings
from .errors import SqlError
from .translator import Translator


class HybridPlan:
    """The SQL fragment and optional in-memory continuation for one pipeline."""

    def __init__(self, *, dialect: str | None = None, sql_statement: Any = None,
                 sql_prefix_ast: Node | None = None,
                 continuation_ast: Node | None = None,
                 continuation_program: Program | None = None,
                 continuation_source_var: str = '_INPUT',
                 pure_sql: bool = False, pure_memory: bool = False,
                 source_tables: list[str] | None = None) -> None:
        self.dialect = dialect
        self.sql_statement = sql_statement
        self._sql_prefix_ast = sql_prefix_ast
        self._continuation_ast = continuation_ast
        self._continuation_program = continuation_program
        self._continuation_source_var = continuation_source_var
        self.pure_sql = bool(pure_sql)
        self.pure_memory = bool(pure_memory)
        self._source_tables = list(source_tables or [])

    @property
    def sql_query(self):
        return self.sql_statement

    @property
    def sqlQuery(self):  # noqa: N802 - cross-host compatibility alias
        return self.sql_statement

    @property
    def sql_prefix_ast(self):
        return self._sql_prefix_ast

    @property
    def continuation_ast(self):
        return self._continuation_ast

    @property
    def continuation_program(self):
        return self._continuation_program

    @property
    def continuation_source_var(self):
        return self._continuation_source_var

    @property
    def is_hybrid(self) -> bool:
        return not self.pure_sql and not self.pure_memory

    @property
    def isHybrid(self) -> bool:  # noqa: N802 - cross-host compatibility alias
        return self.is_hybrid

    @property
    def pureSql(self) -> bool:  # noqa: N802 - cross-host compatibility alias
        return self.pure_sql

    @property
    def pureMemory(self) -> bool:  # noqa: N802 - cross-host compatibility alias
        return self.pure_memory

    @property
    def pure_sql_execution(self) -> bool:
        return self.pure_sql

    @property
    def pure_memory_execution(self) -> bool:
        return self.pure_memory

    @property
    def source_tables(self):
        return self._source_tables


def _try_statement(ast: Node, dialect: str, bindings: Bindings,
                   options: dict[str, Any]) -> Any:
    try:
        translator = Translator(dialect, bindings, options)
        return translator.translate_statement(ast)
    except SqlError:
        return None


def _physical_source(binding: dict[str, Any]) -> str:
    """The physical source a relation binding reads: its table, or for a
    relation query the query text exactly as the application wrote it."""
    from_ = binding.get('from')
    if isinstance(from_, dict) and 'raw' in from_:
        return str(from_['raw'])
    return str(from_)


def _source_tables(ast: Node | None, bindings: Bindings) -> list[str]:
    """Every physical source the tree reads, first use first, each once.
    Keyed by the physical name, so two bindings over one table are one
    source."""
    out: list[str] = []
    seen: set[str] = set()

    def visit(node: Node | None) -> None:
        if node is None:
            return
        if node.t == 'var' and bindings.has(node.name):
            binding = bindings.get(node.name, node.pos)
            if binding['kind'] == 'relation':
                table = _physical_source(binding)
                if table not in seen:
                    seen.add(table)
                    out.append(table)
            return
        for child in node.args:
            visit(child)
        for child in node.items:
            visit(child)
        for child in (node.l, node.r, node.x, node.obj, node.idx,
                      node.target, node.value):
            visit(child)

    visit(ast)
    return out


def _bucket_rows_are_keys(steps: list[Node]) -> bool:
    """Whether the SQL rows for this step list are a bucket's KEYS rather than
    the value SEL would have produced. A BUCKET without a projection is open:
    the translator projects its keys, and SEL's value is a map of member rows.
    The next MAP closes it -- it becomes the bucket's projection, one
    statement, one value in both lanes -- and a FILTER between them is a
    HAVING. Any other step seals it: the members are gone, and no continuation
    can get them back. So a prefix that is open or sealed is not a split
    point, whatever the translator says about it, and the MAP fall-through
    must not fire on a MAP that closes one -- its custom half would be
    evaluated over key rows.
    """
    open_ = False
    for step in steps:
        if step.name == 'BUCKET':
            if open_:
                return True
            open_ = len(step.args) == 2
        elif open_ and step.name == 'MAP':
            open_ = False
        elif open_ and step.name != 'FILTER':
            return True
    return open_


SQL_SPECIAL_CALLS = frozenset({
    'IF', 'COND', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'RECORD', 'LIST',
})


def _contains_unsupported_sql(node: Node | None, dialect: str) -> bool:
    if node is None:
        return False
    if node.t == 'call':
        if node.name not in SQL_SPECIAL_CALLS:
            entry = sqlmap.entry(dialect, 'funcs', ascii_upper(node.name))
            if entry == sqlmap.MISSING or entry is None or isinstance(entry, str):
                return True
        return any(_contains_unsupported_sql(item, dialect) for item in node.args)
    if any(_contains_unsupported_sql(item, dialect) for item in node.args):
        return True
    if any(_contains_unsupported_sql(item, dialect) for item in node.items):
        return True
    return any(_contains_unsupported_sql(item, dialect)
               for item in (node.l, node.r, node.x, node.obj, node.idx,
                            node.target, node.value))


def _field_references(node: Node | None, binder: str = '_') -> list[str]:
    wanted = {binder.upper(), '_', '_1', '_2'}
    out: list[str] = []
    seen: set[str] = set()

    def visit(item: Node | None) -> None:
        if item is None:
            return
        if (item.t == 'index' and item.obj is not None and item.obj.t == 'var'
                and item.idx is not None and item.idx.t == 'text'
                and item.obj.name.upper() in wanted):
            key = str(item.idx.v)
            upper = key.upper()
            if upper not in seen:
                seen.add(upper)
                out.append(key)
        for child in item.args:
            visit(child)
        for child in item.items:
            visit(child)
        for child in (item.l, item.r, item.x, item.obj, item.idx,
                      item.target, item.value):
            visit(child)

    visit(node)
    return out


def _map_record_details(step: Node) -> dict[str, Any] | None:
    args = step.args
    explicit = (len(args) == 3 and args[1].t == 'var'
                and not args[1].grouped)
    body = args[2] if explicit else (args[1] if len(args) > 1 else None)
    if (body is None or body.t != 'call'
            or body.name not in ('RECORD', 'LAZY_RECORD')
            or len(body.args) % 2 != 0):
        return None
    pairs = []
    for i in range(0, len(body.args), 2):
        key = body.args[i]
        if key.t != 'text':
            return None
        pairs.append((key, body.args[i + 1]))
    return {'explicit': explicit, 'binder': args[1].name if explicit else '_',
            'body': body, 'pairs': pairs}


def _try_plan_fallthrough(source: Node, steps: list[Node], dialect: str,
                          catalog: Bindings, options: dict[str, Any]) -> HybridPlan | None:
    map_index = next((i for i, step in enumerate(steps) if step.name == 'MAP'), -1)
    if map_index < 0 or _bucket_rows_are_keys(steps[:map_index]):
        return None
    details = _map_record_details(steps[map_index])
    if details is None:
        return None

    pushable, custom = [], []
    for pair in details['pairs']:
        (custom if _contains_unsupported_sql(pair[1], dialect) else pushable).append(pair)
    if not custom or not pushable:
        return None

    downstream_refs = {
        field.upper()
        for step in steps[map_index + 1:]
        for field in _field_references(step)
    }
    if any(str(pair[0].v).upper() in downstream_refs for pair in custom):
        return None

    projected = {str(pair[0].v).upper() for pair in pushable}
    dependencies: list[str] = []
    dependency_set: set[str] = set()
    for pair in custom:
        for field in _field_references(pair[1], details['binder']):
            upper = field.upper()
            if upper not in projected and upper not in dependency_set:
                dependency_set.add(upper)
                dependencies.append(field)

    rewritten_args: list[Node] = []
    for key, value in pushable:
        rewritten_args.extend((key, value))
    for field in dependencies:
        key = Node('text', steps[map_index].pos, v=field)
        obj = Node('var', steps[map_index].pos, name=details['binder'])
        rewritten_args.extend((key, Node('index', steps[map_index].pos,
                                          obj=obj, idx=key)))

    rewritten_record = copy_node(details['body'])
    rewritten_record.args = rewritten_args
    rewritten_map = copy_node(steps[map_index])
    rewritten_map.args = ([steps[map_index].args[0], steps[map_index].args[1], rewritten_record]
                          if details['explicit']
                          else [steps[map_index].args[0], rewritten_record])
    rewritten_steps = [*steps[:map_index], rewritten_map, *steps[map_index + 1:]]
    rewritten_ast = build_pipeline(source, rewritten_steps)
    sql = _try_statement(rewritten_ast, dialect, catalog, options)
    if sql is None:
        return None

    input_node = Node('var', steps[map_index].pos, name='_INPUT')
    continuation_map = copy_node(steps[map_index])
    continuation_map.args = ([input_node, steps[map_index].args[1], details['body']]
                             if details['explicit']
                             else [input_node, details['body']])
    return HybridPlan(
        dialect=dialect,
        sql_statement=sql,
        sql_prefix_ast=rewritten_ast,
        continuation_ast=continuation_map,
        continuation_program=Program('', continuation_map),
        source_tables=_source_tables(rewritten_ast, catalog),
    )


def _pure_memory_plan(program: Program, dialect: str, catalog: Bindings) -> HybridPlan:
    """The plan for a program nothing of which reaches the database. The
    continuation is the program itself, and the AST it exposes is the program's
    own, so a caller sees the same tree whichever way the plan went."""
    return HybridPlan(dialect=dialect, pure_memory=True,
                      continuation_program=program, continuation_ast=program.ast,
                      source_tables=_source_tables(program.ast, catalog))


def plan_hybrid(program: Program, dialect: str,
                bindings: Bindings | dict[str, Any] | None = None,
                options: dict[str, Any] | None = None) -> HybridPlan:
    """Return the maximal SQL prefix and its optional memory continuation."""
    catalog = bindings if isinstance(bindings, Bindings) else Bindings(bindings or {})
    opts = options or {}
    sqlmap.require_target(dialect)
    catalog.check_aliases()

    # Stage 1 first, exactly as the translator runs it, so the tree unwound
    # below is the one a prefix will be translated from. A program stage 1
    # refuses -- ``A += 1; ...``, a bare statement before the result -- is a
    # program no part of which can be pushed down, which is a pure-memory plan
    # and not an exception: "none of it" is one of the planner's answers.
    try:
        const_names, const_context = sql_constants.scope(catalog)
        normalized = sql_normalise.run(program.ast, const_names, const_context)
    except SqlError:
        return _pure_memory_plan(program, dialect, catalog)
    optimized = optimize_ast_logical(normalized, opts)
    source, steps = unwind_pipeline(optimized)
    if (not steps or source is None or source.t != 'var'
            or not catalog.has(source.name)
            or catalog.get(source.name, source.pos)['kind'] != 'relation'):
        return _pure_memory_plan(program, dialect, catalog)

    full_ast = build_pipeline(source, steps)
    full_sql = _try_statement(full_ast, dialect, catalog, opts)
    if full_sql is not None:
        return HybridPlan(dialect=dialect, sql_statement=full_sql,
                          sql_prefix_ast=full_ast, pure_sql=True,
                          source_tables=_source_tables(full_ast, catalog))

    fallthrough = _try_plan_fallthrough(source, steps, dialect, catalog, opts)
    if fallthrough is not None:
        return fallthrough

    for count in range(len(steps) - 1, 0, -1):
        prefix_steps = steps[:count]
        if _bucket_rows_are_keys(prefix_steps):
            continue
        prefix_ast = build_pipeline(source, prefix_steps)
        sql = _try_statement(prefix_ast, dialect, catalog, opts)
        if sql is None:
            continue
        remaining = steps[count:]
        input_node = Node('var', remaining[0].pos, name='_INPUT')
        continuation_ast = build_pipeline(input_node, remaining)
        return HybridPlan(dialect=dialect, sql_statement=sql,
                          sql_prefix_ast=prefix_ast,
                          continuation_ast=continuation_ast,
                          continuation_program=Program('', continuation_ast),
                          source_tables=_source_tables(prefix_ast, catalog))

    return _pure_memory_plan(program, dialect, catalog)


def execute_hybrid(plan: HybridPlan, db_runner: Callable[[str, list[Value]], Any],
                   context: Value | dict[str, Any] | None = None) -> Any:
    """Execute a pure SQL, pure memory, or split plan."""
    if not isinstance(plan, HybridPlan):
        raise TypeError('execute_hybrid expects a HybridPlan')
    if plan.pure_memory:
        if plan.continuation_program is None:
            raise RuntimeError('a pure-memory hybrid plan has no program')
        return plan.continuation_program.run(context)
    if plan.sql_statement is None:
        raise RuntimeError('a SQL hybrid plan has no statement')
    fragment = plan.sql_statement
    rows = db_runner(fragment.as_statement('params'), fragment.bindings())
    if plan.pure_sql:
        return rows
    if plan.continuation_program is None:
        raise RuntimeError('a hybrid plan has no continuation program')
    root = context.clone() if isinstance(context, Value) else Value.from_native(context or {})
    root.set(plan.continuation_source_var,
             rows if isinstance(rows, Value) else Value.from_native(rows))
    return plan.continuation_program.run(root)


# Public aliases matching the other host APIs.
planHybrid = plan_hybrid
executeHybrid = execute_hybrid
