"""SQL-prefix planning with an in-memory SEL continuation.

The planner follows the Lisp reference implementation: normalize and logically
optimize a relational pipeline, try the complete pipeline first, then try a
safe mixed MAP fall-through, and finally choose the longest translatable prefix.

The contract every host's planner meets is in docs/SQL-TRANSLATION.md §12.1
and is pinned by sql/cases/25-hybrid-plans.sqlt: the planner looks at the
PIPELINE, whichever helper assignments it is written through, and then at the
logical optimiser's rewrite of that (unwinding the raw AST first classified
``X = ORDERS; X .> TAKE(1)`` as pure memory, because a ``seq`` is not a
pipeline; inlining every helper the way stage 1 does for translate() made the
continuation report an error at the helper's definition where run() reports
its use -- see "helper assignments" below for what is done instead),
``source_tables`` names PHYSICAL sources (a relation's ``from``, or a relation
query's text verbatim), a program stage 1 refuses is a pure-memory plan rather
than an exception, and ``options`` is one dict that reaches both the logical
optimiser and the translator.
"""

from __future__ import annotations

from dataclasses import replace
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
from .emit import Emit
from .fragment import Fragment


class HybridPlan:
    """The SQL fragment and optional in-memory continuation for one pipeline."""

    def __init__(self, *, dialect: str | None = None, sql_statement: Any = None,
                 sql_prefix_ast: Node | None = None,
                 continuation_ast: Node | None = None,
                 continuation_program: Program | None = None,
                 continuation_source_var: str = '_INPUT',
                 pure_sql: bool = False, pure_memory: bool = False,
                 source_tables: list[str] | None = None,
                 selected_member: dict[str, str] | None = None) -> None:
        self.dialect = dialect
        self.sql_statement = sql_statement
        self._sql_prefix_ast = sql_prefix_ast
        self._continuation_ast = continuation_ast
        self._continuation_program = continuation_program
        self._continuation_source_var = continuation_source_var
        self.pure_sql = bool(pure_sql)
        self.pure_memory = bool(pure_memory)
        self._source_tables = list(source_tables or [])
        self.selected_member = selected_member

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


def _join_rows_lack_binders(steps: list[Node]) -> bool:
    """Whether the SQL rows for this step list are a join's rows without the
    binders SEL's rows carry. A LINK's row in SEL holds each side under its
    binders and the promoted fields beside them (spec §7.4); SQL carries the
    promoted fields alone. A MAP, a SELECT_COLS or a projected BUCKET after
    the LINK makes the rows exact again -- what they compute is over the
    promoted fields, or is refused -- so a prefix whose LINK nothing has
    projected is not a split point and not a full pushdown (finding Y,
    lanes): its continuation would read ``_["C"]`` where the database sent
    nothing.
    """
    joined = False
    for step in steps:
        if step.name in ('LINK', 'LINK_LEFT'):
            joined = True
        elif step.name in ('MAP', 'SELECT_COLS', 'BUCKET'):
            joined = False
    return joined


def _rows_are_not_the_value(steps: list[Node]) -> bool:
    """The two together: a prefix whose SQL rows are not the value SEL would
    have produced for it, whatever the translator says about it."""
    return _bucket_rows_are_keys(steps) or _join_rows_lack_binders(steps)


SQL_SPECIAL_CALLS = frozenset({
    'IF', 'COND', 'COALESCE', 'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'RECORD', 'LIST',
})


def _contains_unsupported_sql(node: Node | None, dialect: str,
                              defs: dict[str, Node] | None = None,
                              seen: frozenset[str] = frozenset()) -> bool:
    """``defs`` are the helper definitions: a read of one is as unsupported as
    its definition, since the translator will inline it."""
    if node is None:
        return False
    if (node.t == 'var' and defs is not None and node.name in defs
            and node.name not in seen):
        return _contains_unsupported_sql(defs[node.name], dialect, defs,
                                         seen | {node.name})
    if node.t == 'call':
        if node.name not in SQL_SPECIAL_CALLS:
            entry = sqlmap.entry(dialect, 'funcs', ascii_upper(node.name))
            if entry == sqlmap.MISSING or entry is None or isinstance(entry, str):
                return True
        return any(_contains_unsupported_sql(item, dialect, defs, seen)
                   for item in node.args)

    def inner(item: Node | None) -> bool:
        return _contains_unsupported_sql(item, dialect, defs, seen)

    if any(inner(item) for item in node.args):
        return True
    if any(inner(item) for item in node.items):
        return True
    return any(inner(item) for item in (node.l, node.r, node.x, node.obj, node.idx,
                                        node.target, node.value))


def _field_references(node: Node | None, binder: str | None = '_') -> list[str]:
    """The field names read as ``binder["field"]`` in ``node``, first seen
    first and compared exactly: SEL's record keys are case-sensitive, so
    ``name`` and ``Name`` are two fields. ``binder`` None means a read under
    ANY name counts -- a downstream step binds the row however it likes
    (``SORT_BY(s, s["name"])``)."""
    wanted = None if binder is None else {binder.upper(), '_', '_1', '_2'}
    out: list[str] = []
    seen: set[str] = set()

    def visit(item: Node | None) -> None:
        if item is None:
            return
        if (item.t == 'index' and item.obj is not None and item.obj.t == 'var'
                and item.idx is not None and item.idx.t == 'text'
                and (wanted is None or item.obj.name.upper() in wanted)):
            key = str(item.idx.v)
            if key not in seen:
                seen.add(key)
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


# The steps the MAP fall-through may push past the MAP. Each keeps the rows as
# they are -- the same records, fewer or reordered -- so the custom half of the
# projection still runs over its own input. A step that changes the row shape
# (MAP, SELECT_COLS, LINK, BUCKET) would put it over something else, and the
# whole-row comparisons (DEDUPE, DISTINCT, the keyless sorts) would compare the
# dependency columns SQL carries where SEL compares the custom values.
# FILTER retains the MAP's ordinal keys. SQL rows plus a local MAP cannot
# reconstruct those keys after filtering, so this split must not absorb it.
FALLTHROUGH_DOWNSTREAM = frozenset({'SORT_BY', 'TOP_BY', 'TAKE', 'DROP'})


def _reads_whole_row(node: Node | None, binder: str) -> bool:
    """Whether ``node`` reads the row itself -- the binder outside an index
    with a text key, as in ``GET(_, "name")`` or ``COUNT(_)`` -- which no
    projected column can stand in for."""
    wanted = {binder.upper(), '_', '_1', '_2'}

    def visit(item: Node | None) -> bool:
        if item is None:
            return False
        if item.t == 'var' and item.name.upper() in wanted:
            return True
        if (item.t == 'index' and item.obj is not None and item.obj.t == 'var'
                and item.idx is not None and item.idx.t == 'text'):
            # A field read; the object is not a whole-row read.
            return visit(item.idx)
        return (any(visit(child) for child in item.args)
                or any(visit(child) for child in item.items)
                or any(visit(child) for child in (item.l, item.r, item.x, item.obj,
                                                   item.idx, item.target, item.value)))

    return visit(node)


def _is_own_field_read(pair: tuple[Node, Node], binder: str) -> bool:
    """Whether a pushable pair is the plain field read ``binder[key]`` of its
    own key, so that a dependency of the same name may share its column."""
    key, value = pair
    return (value.t == 'index' and value.obj is not None and value.obj.t == 'var'
            and value.idx is not None and value.idx.t == 'text'
            and value.obj.name.upper() == binder.upper()
            and str(value.idx.v) == str(key.v))


def _map_record_details(step: Node) -> dict[str, Any] | None:
    args = step.args
    explicit = (len(args) == 3 and args[1].t == 'var'
                and not args[1].grouped)
    body = args[2] if explicit else (args[1] if len(args) > 1 else None)
    if (body is None or body.t != 'call'
            or body.name != 'RECORD'
            or len(body.args) % 2 != 0):
        return None
    pairs = []
    seen = set()
    for i in range(0, len(body.args), 2):
        key = body.args[i]
        if key.t != 'text':
            return None
        if key.v in seen:
            return None
        seen.add(key.v)
        pairs.append((key, body.args[i + 1]))
    return {'explicit': explicit, 'binder': args[1].name if explicit else '_',
            'body': body, 'pairs': pairs}


def _try_plan_fallthrough(source: Node, steps: list[Node], dialect: str,
                          catalog: Bindings, options: dict[str, Any],
                          helpers: _Helpers) -> HybridPlan | None:
    map_index = next((i for i, step in enumerate(steps) if step.name == 'MAP'), -1)
    if map_index < 0 or _bucket_rows_are_keys(steps[:map_index]):
        return None
    details = _map_record_details(steps[map_index])
    if details is None:
        return None

    pushable, custom = [], []
    for pair in details['pairs']:
        (custom if _contains_unsupported_sql(pair[1], dialect, helpers.defs)
         else pushable).append(pair)
    if not custom or not pushable:
        return None
    # The custom half runs over the rows the SQL returns; a read of the row
    # itself cannot be served by any column.
    if any(_reads_whole_row(pair[1], details['binder']) for pair in custom):
        return None

    # Every step after the MAP goes into the SQL, so each must keep the rows
    # as they are, and may read only what SEL's rows have after the MAP: the
    # pushable keys. The custom keys are not in the SQL; a dependency column
    # is in the SQL but not in SEL's row.
    downstream = steps[map_index + 1:]
    if any(step.name not in FALLTHROUGH_DOWNSTREAM for step in downstream):
        return None
    projected = {str(pair[0].v) for pair in pushable}
    for step in downstream:
        # args[0] is the step's input -- the pipeline so far -- not its own
        # text; the step binds the row under a name of its own, so any read
        # counts.
        for arg in step.args[1:]:
            if any(field not in projected for field in _field_references(arg, None)):
                return None

    # A dependency may share a projected column only when that column IS the
    # field: `"customer_id", _["amount"]` projects amount under the name the
    # custom half would read customer_id by. Names are compared exactly, as
    # SEL compares them; and a dependency that differs from a projected key
    # only by case is not projected beside it, because SQL aliases are not
    # case-sensitive everywhere.
    own = {str(pair[0].v) for pair in pushable
           if _is_own_field_read(pair, details['binder'])}
    # "Case" here is ASCII case, as everywhere in SEL -- never the host's.
    projected_folded = {ascii_upper(key) for key in projected}
    dependencies: list[str] = []
    dependencies_folded: set[str] = set()
    for pair in custom:
        for field in _field_references(pair[1], details['binder']):
            if field in projected:
                if field not in own:
                    return None
            elif ascii_upper(field) in projected_folded:
                return None
            elif field not in dependencies:
                # Two dependencies must not differ only by case either.
                if ascii_upper(field) in dependencies_folded:
                    return None
                dependencies_folded.add(ascii_upper(field))
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
    rewritten_ast = helpers.wrap(build_pipeline(source, rewritten_steps))
    sql = _try_statement(rewritten_ast, dialect, catalog, options)
    if sql is None:
        return None

    # The continuation re-applies the projection to the rows that come back:
    # a pushable pair is passed through BY KEY -- the SQL already computed it,
    # under that name -- and a custom pair is evaluated as written, over the
    # dependency columns projected beside it.
    input_node = Node('var', steps[map_index].pos, name='_INPUT')
    continuation_args: list[Node] = []
    for key, value in details['pairs']:
        if any(pair[0] is key for pair in pushable):
            obj = Node('var', value.pos, name=details['binder'])
            continuation_args.extend((key, Node('index', value.pos, obj=obj, idx=key)))
        else:
            continuation_args.extend((key, value))
    continuation_record = copy_node(details['body'])
    continuation_record.args = continuation_args
    continuation_map = copy_node(steps[map_index])
    continuation_map.args = ([input_node, steps[map_index].args[1], continuation_record]
                             if details['explicit']
                             else [input_node, continuation_record])
    continuation_ast = helpers.wrap(continuation_map)
    return HybridPlan(
        dialect=dialect,
        sql_statement=sql,
        sql_prefix_ast=rewritten_ast,
        continuation_ast=continuation_ast,
        continuation_program=Program('', continuation_ast),
        source_tables=helpers.tables(rewritten_ast),
    )


# --- helper assignments -------------------------------------------------------
#
# Stage 1 inlines a helper assignment for translate(): ``Y = "x"; ... + Y`` is
# rendered as ``... + "x"``, the literal keeping its definition-site position,
# which is right for a refusal message. It is wrong for the memory half of a
# plan, because that half is a program run() evaluates and §12.1 promises it
# reports errors where run() would: run() evaluates the READ of Y at the use
# site and reports ``+``'s operand there, and it evaluates the definition once,
# before the pipeline, not once per row (review 2026-09-15 finding AJ). So
# the planner does not inline. It plans the program as written, three ways:
#
#   * A helper that IS a literal -- after inlining earlier such helpers and
#     folding, ``N = 1 + 1`` as much as ``N = 2`` -- is inlined at its reads,
#     stamped with the read's position. That is invisible: a leaf literal
#     cannot fail, and neither can the read, since the definition exists. It
#     keeps ``TAKE(N)`` a ``LIMIT 2`` rather than a helper the SQL has to carry.
#   * A helper read as the pipeline's SOURCE is unwound through: ``X = ORDERS
#     .> TAKE(2); X .> MAP(...)`` is one pipeline over ORDERS, so the prefix
#     search sees every step. Only the source position looks through a
#     helper; a read anywhere else stays a read.
#   * What is handed to the translator, and what is kept for the
#     continuation, carries in front of it the assignments it still reads and
#     the ones those read, in program order, as the program wrote them. The
#     translator runs its own stage 1 over that seq and inlines; the
#     continuation evaluates them once, before its steps, as run() does. An
#     assignment nothing after the split reads is dropped, as stage 1 drops
#     it for translate() -- the one departure, and the same one.

LITERAL_TYPES = frozenset({'num', 'text', 'bool', 'null'})


def _statements(ast: Node) -> tuple[list[Node], Node]:
    """The leading statements and the result expression of a program."""
    if ast.t != 'seq':
        return [], ast
    return ast.items[:-1], ast.items[-1]


def _assigned_name(statement: Node) -> str:
    """The name a leading statement assigns. Stage 1 has accepted every
    statement by the time this runs, so each is an assignment whose target is
    a name, or a name indexed by constants."""
    target = statement.target
    while target.t == 'index':
        target = target.obj
    return target.name


def _definitions(leading: list[Node]) -> dict[str, Node]:
    """The whole-name definitions, by name. Stage 1 refuses a name assigned
    twice, or both whole and by index, so each name here has exactly one."""
    defs: dict[str, Node] = {}
    for s in leading:
        if s.target.t == 'var':
            defs[s.target.name] = s.value
    return defs


def _inline_literals(node: Node | None, literals: dict[str, Node],
                     bound: list[str] | None = None) -> Node | None:
    """``node`` with every read of a literal helper replaced by the literal,
    stamped with the read's position. Binder scoping is stage 1's: a binder
    shadows a same-named helper inside its body. Copies on the way down,
    never writes."""
    if node is None:
        return node
    if bound is None:
        bound = []
    t = node.t
    if t == 'var':
        if node.name in bound or node.name not in literals:
            return node
        return replace(literals[node.name], pos=node.pos)
    if t in LITERAL_TYPES:
        return node

    def inline(child: Node | None, scope: list[str] = bound) -> Node | None:
        return _inline_literals(child, literals, scope)

    if t == 'un':
        return replace(node, x=inline(node.x))
    if t == 'bin':
        return replace(node, l=inline(node.l), r=inline(node.r))
    if t == 'index':
        return replace(node, obj=inline(node.obj), idx=inline(node.idx))
    if t in ('list', 'seq'):
        return replace(node, items=[inline(item) for item in node.items])
    if t == 'assign':
        return replace(node, value=inline(node.value))
    if t == 'call':
        inner = list(bound)
        binds = node.spec is not None and node.spec.binds
        if binds:
            inner.append('_K')
            inner.append(node.args[1].name
                         if len(node.args) == 3 and sql_constants.is_binder_name(node.args[1])
                         else '_')
        args: list[Node] = []
        for i, arg in enumerate(node.args):
            if (binds and i == 1 and len(node.args) == 3
                    and sql_constants.is_binder_name(arg)):
                args.append(arg)
            else:
                args.append(inline(arg, bound if i == 0 else inner))
        return replace(node, args=args)
    return node


def _literal_helpers(leading: list[Node], options: dict[str, Any]) -> dict[str, Node]:
    """The literal helpers: each whole-name definition, after the earlier
    literal helpers are inlined into it and it is folded, when what is left
    is a leaf."""
    literals: dict[str, Node] = {}
    for s in leading:
        if s.target.t != 'var':
            continue
        folded = optimize_ast_logical(_inline_literals(s.value, literals), options)
        if folded.t in LITERAL_TYPES:
            literals[s.target.name] = folded
    return literals


def _unwind_through_helpers(result: Node, defs: dict[str, Node],
                            literals: dict[str, Node]) -> tuple[Node | None, list[Node]]:
    """The pipeline the planner probes: the result unwound, and where its
    source is a helper, that helper's definition unwound in turn."""
    source, steps = unwind_pipeline(_inline_literals(result, literals))
    seen: set[str] = set()
    while (source is not None and source.t == 'var' and source.name in defs
           and source.name not in seen):
        seen.add(source.name)
        inner_source, inner_steps = unwind_pipeline(
            _inline_literals(defs[source.name], literals))
        source = inner_source
        steps = [*inner_steps, *steps]
    return source, steps


def _read_names(node: Node | None, out: set[str] | None = None) -> set[str]:
    """The names a tree reads, binders included: an over-approximation that
    can only keep an assignment the tree does not need, never drop one it
    does."""
    if out is None:
        out = set()
    if node is None:
        return out
    if node.t == 'var':
        out.add(node.name)
        return out
    for item in node.args:
        _read_names(item, out)
    for item in node.items:
        _read_names(item, out)
    for child in (node.l, node.r, node.x, node.obj, node.idx, node.target, node.value):
        _read_names(child, out)
    return out


def _referenced_assignments(leading: list[Node], node: Node) -> list[Node]:
    """The leading assignments ``node`` depends on, in program order: those
    whose name it reads, and those THEY read, transitively."""
    needed = _read_names(node)
    grew = True
    while grew:
        grew = False
        for s in leading:
            if _assigned_name(s) not in needed:
                continue
            for name in _read_names(s.value):
                if name not in needed:
                    needed.add(name)
                    grew = True
    return [s for s in leading if _assigned_name(s) in needed]


def _with_helpers(leading: list[Node], node: Node) -> Node:
    """``node`` behind the assignments it depends on, as the program wrote
    them -- a seq the translator's stage 1 inlines and the evaluator runs in
    order -- or ``node`` itself when it depends on none."""
    kept = _referenced_assignments(leading, node)
    return Node('seq', kept[0].pos, items=[*kept, node]) if kept else node


class _Helpers:
    """What the split points need of the helper assignments: the definitions
    (for the MAP fall-through's classification), the wrap, and the physical
    sources of a wrapped tree."""

    def __init__(self, defs: dict[str, Node], wrap: Callable[[Node], Node],
                 tables: Callable[[Node], list[str]]) -> None:
        self.defs = defs
        self.wrap = wrap
        self.tables = tables


def _pure_memory_plan(program: Program, dialect: str, catalog: Bindings) -> HybridPlan:
    """The plan for a program nothing of which reaches the database. The
    continuation is the program itself, and the AST it exposes is the program's
    own, so a caller sees the same tree whichever way the plan went."""
    return HybridPlan(dialect=dialect, pure_memory=True,
                      continuation_program=program, continuation_ast=program.ast,
                      source_tables=_source_tables(program.ast, catalog))


def _latest_field_name(node):
    return (node.idx.v if node is not None and node.t == 'index'
            and node.obj.t == 'var' and node.obj.name == '_' and node.idx.t == 'text' else None)


def _try_latest_member(source, steps, dialect, catalog, opts, helpers):
    """A schema-proven unique TOP 1, not an arbitrary bare-bucket split."""
    if dialect not in ('mariadb', 'mysql', 'postgresql', 'sqlite'):
        return None
    rel = catalog.get(source.name, source.pos)
    revision = rel.get('unique_key')
    at = next((i for i, s in enumerate(steps) if s.name == 'BUCKET'), None)
    if revision is None or at is None or not isinstance(rel['from'], str) or rel.get('correlate'):
        return None
    ba = steps[at].args
    partition = _latest_field_name(ba[1]) if len(ba) in (2, 3) else None
    body = ba[2] if len(ba) == 3 else None
    if body is None and at + 1 < len(steps):
        m = steps[at + 1]
        if m.name == 'MAP' and len(m.args) == 2:
            body = m.args[1]
    pf = rel['fields'].get(ascii_upper(partition or ''), {})
    rf = rel['fields'].get(ascii_upper(revision), {})
    if (partition is None or body is None or body.t != 'call' or body.name != 'RECORD'
            or len(body.args) != 4 or pf.get('type') not in ('NUM', 'TEXT') or rf.get('type') != 'NUM'
            or pf.get('column') != partition or rf.get('column') != revision
            or pf.get('raw') or rf.get('raw') or rf.get('guard')):
        return None
    ra = body.args
    if ra[0].t != 'text' or ra[2].t != 'text' or ra[0].v == ra[2].v:
        return None
    values = [ra[1], ra[3]]
    top = next((n for n in values if n.t == 'call' and n.name == 'TOP_BY'), None)
    if top is None or not any(n.t == 'var' and n.name == '_K' for n in values):
        return None
    ta = top.args
    if (len(ta) != 4 or ta[0].t != 'var' or ta[0].name != '_'
            or _latest_field_name(ta[1]) != revision or ta[2].t != 'text' or ta[2].v != 'DESC'
            or ta[3].t != 'num' or ta[3].v != '1'):
        return None
    for s in steps[:at]:
        if s.name == 'FILTER':
            continue
        if (s.name != 'SORT_BY' or len(s.args) not in (2, 3)
                or _latest_field_name(s.args[1]) != revision
                or (len(s.args) == 3 and (s.args[2].t != 'text' or s.args[2].v != 'ASC'))):
            return None
    dummy = Node('call', source.pos, name='FILTER', args=[source, Node('bool', source.pos, v=True)])
    prefix = helpers.wrap(build_pipeline(source, steps[:at] or [dummy]))
    sql = _try_statement(prefix, dialect, catalog, opts)
    if sql is None:
        return None
    try:
        emit = Emit(dialect)
        input_name, groups_name = '_sel_input', '_sel_latest'
        while ascii_upper(input_name) == ascii_upper(rel['from']):
            input_name += '_'
        while ascii_upper(groups_name) in (ascii_upper(rel['from']), ascii_upper(input_name)):
            groups_name += '_'
        qi, qg, qr, qmax, qfirst = map(emit.ident, [input_name, groups_name, revision, '_sel_revision', '_sel_first'])
        key = emit.text_operand(Fragment([emit.ident(partition)], pf['type'], dialect)).as_value()
        parts = [f'WITH {qi} AS (', *sql.parts,
                 f'), {qg} AS (SELECT MAX({qr}) AS {qmax}, MIN({qr}) AS {qfirst} FROM {qi} GROUP BY {key}) '
                 f'SELECT {qi}.* FROM {qi} JOIN {qg} ON {qi}.{qr} = {qg}.{qmax} ORDER BY {qg}.{qfirst} ASC']
        continuation = helpers.wrap(build_pipeline(Node('var', steps[at].pos, name='_INPUT'), steps[at:]))
        return HybridPlan(dialect=dialect,
                          sql_statement=Fragment(parts, 'STATEMENT', dialect, sql.params, sql.param_kinds, sql.caveats),
                          sql_prefix_ast=prefix, continuation_ast=continuation,
                          continuation_program=Program('', continuation), source_tables=[rel['from']],
                          selected_member={'partition_key': partition, 'revision_key': revision})
    except SqlError:
        return None


def plan_hybrid(program: Program, dialect: str,
                bindings: Bindings | dict[str, Any] | None = None,
                options: dict[str, Any] | None = None) -> HybridPlan:
    """Return the maximal SQL prefix and its optional memory continuation."""
    catalog = bindings if isinstance(bindings, Bindings) else Bindings(bindings or {})
    opts = options or {}
    sqlmap.require_target(dialect)
    catalog.check_aliases()

    # Stage 1 first, exactly as the translator runs it, for its verdict. A
    # program stage 1 refuses -- ``A += 1; ...``, a bare statement before the
    # result -- is a program no part of which can be pushed down, which is a
    # pure-memory plan and not an exception: "none of it" is one of the
    # planner's answers. Its TREE is not what is planned, though: see "helper
    # assignments" above.
    const_names, const_context = sql_constants.scope(catalog)
    try:
        normalized = sql_normalise.run(program.ast, const_names, const_context)
        identity_barrier = sql_constants.identity_loss_before_grouping(normalized)
    except SqlError:
        return _pure_memory_plan(program, dialect, catalog)
    leading, result = _statements(program.ast)
    literals = _literal_helpers(leading, opts)
    defs = _definitions(leading)

    def is_relation(node: Node | None) -> bool:
        return (node is not None and node.t == 'var' and catalog.has(node.name)
                and catalog.get(node.name, node.pos)['kind'] == 'relation')

    unwound_source, unwound_steps = _unwind_through_helpers(result, defs, literals)
    if not unwound_steps or not is_relation(unwound_source):
        return _pure_memory_plan(program, dialect, catalog)
    optimized = optimize_ast_logical(build_pipeline(unwound_source, unwound_steps), opts)
    source, steps = unwind_pipeline(optimized)
    if not steps or not is_relation(source):
        return _pure_memory_plan(program, dialect, catalog)

    helpers = _Helpers(
        defs,
        lambda node: _with_helpers(leading, node),
        # The physical sources of a wrapped tree are read off what the
        # translator renders: stage 1's tree, where an assignment a binder
        # shadows is gone.
        lambda wrapped: _source_tables(
            sql_normalise.run(wrapped, const_names, const_context), catalog),
    )

    # The whole pipeline, unless its rows would be a bucket's keys: the
    # translator renders a bare bucket as its keys, and a plan that pushes the
    # whole of ``... .> BUCKET(k)`` would hand them back as the answer.
    full_ast = helpers.wrap(build_pipeline(source, steps))
    full_sql = (None if identity_barrier or _rows_are_not_the_value(steps)
                else _try_statement(full_ast, dialect, catalog, opts))
    if full_sql is not None:
        return HybridPlan(dialect=dialect, sql_statement=full_sql,
                          sql_prefix_ast=full_ast, pure_sql=True,
                          source_tables=helpers.tables(full_ast))

    latest = _try_latest_member(source, steps, dialect, catalog, opts, helpers)
    if latest is not None:
        return latest

    fallthrough = (None if identity_barrier else
                   _try_plan_fallthrough(source, steps, dialect, catalog, opts, helpers))
    if fallthrough is not None:
        return fallthrough

    for count in range(len(steps) - 1, 0, -1):
        prefix_steps = steps[:count]
        if _rows_are_not_the_value(prefix_steps):
            continue
        prefix_ast = helpers.wrap(build_pipeline(source, prefix_steps))
        if identity_barrier:
            try:
                normalized_prefix = sql_normalise.run(prefix_ast, const_names, const_context)
                if sql_constants.identity_loss_before_grouping(normalized_prefix, True):
                    continue
            except SqlError:
                continue
        sql = _try_statement(prefix_ast, dialect, catalog, opts)
        if sql is None:
            continue
        remaining = steps[count:]
        input_node = Node('var', remaining[0].pos, name='_INPUT')
        continuation_ast = helpers.wrap(build_pipeline(input_node, remaining))
        return HybridPlan(dialect=dialect, sql_statement=sql,
                          sql_prefix_ast=prefix_ast,
                          continuation_ast=continuation_ast,
                          continuation_program=Program('', continuation_ast),
                          source_tables=helpers.tables(prefix_ast))

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
