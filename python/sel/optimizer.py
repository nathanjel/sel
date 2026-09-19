"""Engine-independent logical and physical pipeline rewrites.

The AST is treated as immutable by callers.  Rewrites copy nodes as they are
visited, which lets the same optimizer feed the in-memory evaluator and the SQL
translator without changing a compiled program behind the host's back.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import decimal as D
from .errors import MAX_DEPTH, SelError
from .eval import bytes_compare
from .math_plan import compile_math_plan, is_math_op
from .parser import Node
from .registry import lookup
from .utf8 import encode_utf8


PIPELINE_OPS = frozenset({
    'FILTER', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
    'TAKE', 'DROP', 'SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY',
    'LINK', 'LINK_LEFT',
})


def copy_node(node: Node | None) -> Node | None:
    if node is None:
        return None
    return replace(node, args=list(node.args), items=list(node.items))


def call(name: str, args: list[Node], pos) -> Node:
    return Node('call', pos, name=name, spec=lookup(name), args=args)


def unwind_pipeline(node: Node | None) -> tuple[Node | None, list[Node]]:
    steps = []
    current = node
    while (current is not None and current.t == 'call'
           and current.name in PIPELINE_OPS and current.args):
        steps.insert(0, current)
        current = current.args[0]
    return current, steps


def build_pipeline(source: Node | None, steps: list[Node]) -> Node | None:
    current = source
    for step in steps:
        next_node = copy_node(step)
        next_node.args = [current, *step.args[1:]]
        current = next_node
    return current


def literal_bool(value: bool, pos) -> Node:
    return Node('bool', pos, v=value)


def literal_num(value: str, pos) -> Node:
    return Node('num', pos, v=value)


# A fold that replaces a node by one of its children must not move the error
# position an operator over the result reports: spec §6.3 names the node that
# actually failed, and to the operator the operand IS the folded node, not the
# literal inside it (ctl.if.constant-condition-result-keeps-the-if-position).
# So a hoisted child is re-stamped with the folded node's position -- which is
# only exact for a leaf literal, the one shape that carries no positions of
# its own and cannot fail by itself. A variable is a leaf that can (E_UNDEF_VAR
# at its own column), so it is not a literal here.
LITERAL_TYPES = frozenset(('num', 'text', 'bool', 'null'))


def is_literal(node: Node | None) -> bool:
    return node is not None and node.t in LITERAL_TYPES


def hoist_literal(child: Node, pos) -> Node:
    return replace(child, pos=pos)


def text_compare(a: str, b: str) -> int:
    return bytes_compare(encode_utf8(a), encode_utf8(b))


def fold(node: Node | None) -> Node | None:
    if node is None:
        return None
    if node.t == 'un' and node.x is not None:
        if node.op == 'NOT' and node.x.t == 'bool':
            return literal_bool(not node.x.v, node.pos)
        if node.op == 'NEG' and node.x.t == 'num':
            try:
                return literal_num(D.format(D.negate(D.parse(node.x.v, node.pos))), node.pos)
            except Exception:
                return node
        return node
    if node.t == 'bin' and node.l is not None and node.r is not None:
        if node.op == 'AND':
            if node.l.t == 'bool' and not node.l.v:
                return literal_bool(False, node.pos)
            if node.l.t == 'bool' and node.r.t == 'bool':
                return literal_bool(node.l.v and node.r.v, node.pos)
        if node.op == 'OR':
            if node.l.t == 'bool' and node.l.v:
                return literal_bool(True, node.pos)
            if node.l.t == 'bool' and node.r.t == 'bool':
                return literal_bool(node.l.v or node.r.v, node.pos)
        if (node.l.t == 'num' and node.r.t == 'num'
                and node.op in ('+', '-', '*', '/', '%')):
            try:
                left = D.parse(node.l.v, node.pos)
                right = D.parse(node.r.v, node.pos)
                if node.op == '+':
                    result = D.add(left, right, node.pos)
                elif node.op == '-':
                    result = D.sub(left, right, node.pos)
                elif node.op == '*':
                    result = D.mul(left, right, node.pos)
                elif node.op == '/':
                    result = D.div(left, right, node.pos)
                else:
                    result = D.mod(left, right, node.pos)
                return literal_num(D.format(result), node.pos)
            except Exception:
                return node
        if (node.l.t == 'num' and node.r.t == 'num'
                and node.op in ('==', '!=', '<', '<=', '>', '>=')):
            try:
                c = D.cmp(D.parse(node.l.v, node.pos), D.parse(node.r.v, node.pos))
                if node.op == '==':
                    value = c == 0
                elif node.op == '!=':
                    value = c != 0
                elif node.op == '<':
                    value = c < 0
                elif node.op == '<=':
                    value = c <= 0
                elif node.op == '>':
                    value = c > 0
                else:
                    value = c >= 0
                return literal_bool(value, node.pos)
            except Exception:
                return node
        if (node.l.t == 'text' and node.r.t == 'text'
                and node.op in ('$==', '$!=', '$<', '$<=', '$>', '$>=')):
            c = text_compare(node.l.v, node.r.v)
            if node.op == '$==':
                value = c == 0
            elif node.op == '$!=':
                value = c != 0
            elif node.op == '$<':
                value = c < 0
            elif node.op == '$<=':
                value = c <= 0
            elif node.op == '$>':
                value = c > 0
            else:
                value = c >= 0
            return literal_bool(value, node.pos)
        return node
    if (node.t == 'call' and node.name == 'IF' and len(node.args) == 3
            and node.args[0].t == 'bool'):
        branch = node.args[1] if node.args[0].v else node.args[2]
        return hoist_literal(branch, node.pos) if is_literal(branch) else node
    return node


def field_refs(node: Node | None, binder: str = '_') -> list[str]:
    result: list[str] = []

    def visit(item: Node | None) -> None:
        if item is None:
            return
        if (item.t == 'index' and item.obj is not None and item.obj.t == 'var'
                and item.idx is not None and item.idx.t == 'text'
                and any(name.upper() == item.obj.name.upper()
                        for name in (binder, '_', '_1', '_2'))):
            result.append(item.idx.v)
        for child in item.args:
            visit(child)
        for child in item.items:
            visit(child)
        visit(item.l)
        visit(item.r)
        visit(item.x)
        visit(item.target)
        visit(item.value)
        if item.t == 'index':
            visit(item.obj)
            visit(item.idx)

    visit(node)
    return list(dict.fromkeys(result))


def reads_var(node: Node | None, names: tuple[str, ...]) -> bool:
    """Whether ``node`` reads one of ``names`` as a variable -- other than as
    ``name["field"]``, which is a field read. Case-insensitively, like the
    evaluator's frames."""
    wanted = {name.upper() for name in names}
    found = False

    def visit(item: Node | None) -> None:
        nonlocal found
        if item is None or found:
            return
        if item.t == 'var' and item.name.upper() in wanted:
            found = True
            return
        if (item.t == 'index' and item.obj is not None and item.obj.t == 'var'
                and item.idx is not None and item.idx.t == 'text'):
            return
        for child in item.args:
            visit(child)
        for child in item.items:
            visit(child)
        visit(item.l)
        visit(item.r)
        visit(item.x)
        visit(item.obj)
        visit(item.idx)
        visit(item.target)
        visit(item.value)

    visit(node)
    return found


def reads_row_or_key(node: Node | None, binder: str = '_') -> bool:
    """Whether a body reads the element as a whole (its binder, or any of the
    pipeline's implicit names) or its key ``_K``. The field set says what a
    rewrite may rely on; this says when it may not: a body that reads either
    cannot move across a step that changes the rows' shape (MAP, SELECT_COLS)
    or renumbers them (MAP, SELECT_COLS, the sorts)."""
    return reads_var(node, (binder, '_', '_1', '_2', '_K'))


def step_reads_key(step: Node) -> bool:
    """Whether a step's own arguments (not its input) read ``_K``: the keys a
    sort renumbers, so such a step keeps its place relative to one."""
    return any(reads_var(arg, ('_K',)) for arg in step.args[1:])


def keys_renumbered_by(step: Node | None) -> bool:
    """Whether the step after a FILTER hides where the FILTER ran. FILTER keeps
    its input's keys (spec §7.3) and MAP, SELECT_COLS and the sorts renumber,
    so a FILTER moved in front of one of them carries the source's keys where
    the program as written carried the step's -- visible in the answer, and in
    any later ``_K``. Only a following step that renumbers again without
    reading ``_K`` hides that; the end of the pipeline, or another FILTER,
    does not."""
    return step is not None and step.name != 'FILTER' and not step_reads_key(step)


def source_is_list(source: Node | None) -> bool:
    """Whether the source a pipeline starts from is a list already, so a FILTER
    whose predicate is a constant TRUE over it is the identity. Over a scalar
    it is not: FILTER wraps a scalar into a one-element list (spec §7.3), and
    only a later step, a list literal or a constructor is known not to be one."""
    return source is not None and (
        source.t == 'list' or (source.t == 'call' and source.name in ('LIST', 'RECORD')))


def map_details(step: Node) -> dict[str, Any]:
    args = step.args
    explicit = len(args) == 3 and args[1].t == 'var' and not args[1].grouped
    return {'binder': args[1].name if explicit else '_',
            'body': args[2] if explicit else args[1], 'explicit': explicit}


def map_passthroughs(step: Node) -> list[str]:
    details = map_details(step)
    body = details['body']
    if body is None or body.t != 'call' or body.name != 'RECORD':
        return []
    fields = []
    for i in range(0, len(body.args) - 1, 2):
        key, value = body.args[i], body.args[i + 1]
        if (key.t == 'text' and value.t == 'index' and value.obj.t == 'var'
                and value.obj.name.upper() == details['binder'].upper()
                and value.idx.t == 'text' and value.idx.v == key.v):
            fields.append(key.v)
    return fields


def map_has_computed_fields(step: Node) -> bool:
    body = map_details(step)['body']
    if body is None or body.t != 'call' or body.name != 'RECORD':
        return True
    return len(map_passthroughs(step)) * 2 != len(body.args)


def filter_details(step: Node) -> dict[str, Any]:
    args = step.args
    explicit = len(args) == 3 and args[1].t == 'var' and not args[1].grouped
    return {'binder': args[1].name if explicit else '_',
            'predicate': args[2] if explicit else args[1],
            'explicit': explicit, 'valid': len(args) == 2 or explicit}


def sort_details(step: Node) -> dict[str, Any]:
    args = step.args
    count = len(args)
    binder, key = '_', None
    if step.name in ('SORT', 'SORT_DESC'):
        if count == 1:
            return {'binder': None, 'key': None}
        binder = args[1].name if count == 3 and args[1].t == 'var' and not args[1].grouped else '_'
        key = args[2] if count == 3 else args[1]
    elif step.name in ('TOP', 'TOP_DESC'):
        if count == 2:
            return {'binder': None, 'key': None}
        sort_count = count - 1
        # TOP(source, key, n) has three arguments and
        # TOP(source, binder, key, n) has four.  `sort_count` excludes the
        # final n, so the explicit-binder form is 3, not 4.
        binder = args[1].name if sort_count == 3 and args[1].t == 'var' and not args[1].grouped else '_'
        key = args[2] if sort_count == 3 else args[1]
    elif step.name in ('SORT_BY', 'TOP_BY'):
        sort_count = count - 1 if step.name == 'TOP_BY' else count
        if sort_count == 2 or (sort_count == 3 and args[2].t == 'text'):
            key = args[1]
        elif sort_count == 3 and args[1].t == 'var' and not args[1].grouped:
            binder, key = args[1].name, args[2]
        elif args[1].t == 'var' and not args[1].grouped:
            binder, key = args[1].name, args[2]
    return {'binder': binder, 'key': key}


def select_fields(step: Node) -> list[str]:
    result = []
    for arg in step.args[1:]:
        if arg.t == 'list':
            result.extend(item.v for item in arg.items if item.t == 'text')
        elif arg.t == 'text':
            result.append(arg.v)
    return result


def numeric_literal(node: Node | None) -> int | None:
    if node is None or node.t != 'num':
        return None
    try:
        value = D.parse(node.v)
        if value is None or not D.is_integer(value):
            return None
        result = D.to_safe_int(value)
        return result if result >= 0 else None
    except Exception:
        return None


def split_and(node: Node | None) -> list[Node]:
    return split_and(node.l) + split_and(node.r) if node is not None and node.t == 'bin' and node.op == 'AND' else [node]


def combine_and(nodes: list[Node], pos=None) -> Node | None:
    if not nodes:
        return None
    result = nodes[0]
    for node in nodes[1:]:
        result = Node('bin', pos or result.pos, op='AND', l=result, r=node)
    return result


def rename_var(node: Node | None, old_name: str, new_name: str) -> Node | None:
    if node is None:
        return None
    copy = copy_node(node)
    if copy.t == 'var' and copy.name.upper() == old_name.upper():
        copy.name = new_name
    copy.args = [rename_var(item, old_name, new_name) for item in copy.args]
    copy.items = [rename_var(item, old_name, new_name) for item in copy.items]
    if copy.l is not None:
        copy.l = rename_var(copy.l, old_name, new_name)
    if copy.r is not None:
        copy.r = rename_var(copy.r, old_name, new_name)
    if copy.x is not None:
        copy.x = rename_var(copy.x, old_name, new_name)
    if copy.obj is not None:
        copy.obj = rename_var(copy.obj, old_name, new_name)
    if copy.idx is not None:
        copy.idx = rename_var(copy.idx, old_name, new_name)
    if copy.target is not None:
        copy.target = rename_var(copy.target, old_name, new_name)
    if copy.value is not None:
        copy.value = rename_var(copy.value, old_name, new_name)
    return copy


def step_arg_options(step: Node, index: int, options: dict[str, Any]) -> dict[str, Any]:
    """The evaluator resolves the three-argument SORT_BY / TOP_BY form by shape
    (spec §7.3): a text literal in the third slot is the direction, otherwise a
    bare name in the second slot is the binder and the third slot is its key.
    A fold that hoists a text literal into that slot -- ``IF(TRUE, "DESC",
    "ASC")`` -- would change the form, so the slot is walked without folding."""
    sort_count = (len(step.args) if step.name == 'SORT_BY'
                  else len(step.args) - 1 if step.name == 'TOP_BY' else 0)
    if (sort_count == 3 and index == 2 and step.args[1].t == 'var'
            and not step.args[1].grouped):
        return {**options, 'foldConstants': False}
    return options


def logical_steps(source: Node | None, steps: list[Node],
                  options: dict[str, Any] | None = None) -> list[Node]:
    options = options or {}
    current = steps
    changed = True
    while changed:
        changed = False
        next_steps = []
        i = 0
        while i < len(current):
            first = current[i]
            second = current[i + 1] if i + 1 < len(current) else None
            if (second is not None and first.name == 'TAKE' and second.name == 'TAKE'
                    and len(first.args) == 2 and len(second.args) == 2):
                left, right = numeric_literal(first.args[1]), numeric_literal(second.args[1])
                if left is not None and right is not None:
                    merged = copy_node(first)
                    merged.args = [first.args[0], literal_num(str(min(left, right)), second.args[1].pos)]
                    next_steps.append(merged)
                    i += 2
                    changed = True
                    continue
            if (second is not None and first.name == 'DROP' and second.name == 'DROP'
                    and len(first.args) == 2 and len(second.args) == 2):
                left, right = numeric_literal(first.args[1]), numeric_literal(second.args[1])
                if left is not None and right is not None:
                    merged = copy_node(first)
                    merged.args = [first.args[0], literal_num(str(left + right), second.args[1].pos)]
                    next_steps.append(merged)
                    i += 2
                    changed = True
                    continue
            if (second is not None and second.name == 'TAKE'
                    and first.name in ('SORT', 'SORT_DESC', 'SORT_BY')
                    and len(second.args) == 2):
                top_name = 'TOP' if first.name == 'SORT' else 'TOP_DESC' if first.name == 'SORT_DESC' else 'TOP_BY'
                merged = copy_node(first)
                merged.name = top_name
                merged.spec = lookup(top_name)
                merged.args = [*first.args, second.args[1]]
                next_steps.append(merged)
                i += 2
                changed = True
                continue
            if second is not None and first.name == 'MAP' and second.name == 'FILTER':
                passes = map_passthroughs(first)
                details = filter_details(second)
                refs = field_refs(details['predicate'], details['binder'])
                if (details['valid'] and refs and all(field in passes for field in refs)
                        and not reads_row_or_key(details['predicate'], details['binder'])
                        and keys_renumbered_by(current[i + 2] if i + 2 < len(current) else None)):
                    next_steps.extend((second, first))
                    i += 2
                    changed = True
                    continue
            if (second is not None and first.name in ('SORT', 'SORT_DESC', 'SORT_BY')
                    and second.name == 'FILTER' and not step_reads_key(second)
                    and keys_renumbered_by(current[i + 2] if i + 2 < len(current) else None)):
                next_steps.extend((second, first))
                i += 2
                changed = True
                continue
            if second is not None and first.name == 'SELECT_COLS' and second.name == 'FILTER':
                details = filter_details(second)
                refs = field_refs(details['predicate'], details['binder'])
                if (details['valid'] and refs and all(field in select_fields(first) for field in refs)
                        and not reads_row_or_key(details['predicate'], details['binder'])
                        and keys_renumbered_by(current[i + 2] if i + 2 < len(current) else None)):
                    next_steps.extend((second, first))
                    i += 2
                    changed = True
                    continue
            if (second is not None and first.name == 'MAP'
                    and second.name in ('TOP', 'TOP_DESC', 'TOP_BY', 'SORT', 'SORT_DESC', 'SORT_BY')
                    and map_has_computed_fields(first)):
                # Only a key over pass-through fields is the same value before the
                # MAP: a keyless sort compares the MAP's outputs, and a key that
                # reads the whole row or `_K` reads what the MAP changes.
                details = sort_details(second)
                refs = field_refs(details['key'], details['binder'] or '_') if details['key'] else []
                if (details['key'] and refs and all(field in map_passthroughs(first) for field in refs)
                        and not reads_row_or_key(details['key'], details['binder'] or '_')):
                    next_steps.extend((second, first))
                    i += 2
                    changed = True
                    continue
            if (options.get('fuseFilters', True) is not False
                    and second is not None and first.name == 'FILTER' and second.name == 'FILTER'):
                left, right = filter_details(first), filter_details(second)
                if not left['valid'] or not right['valid']:
                    next_steps.append(first)
                    i += 1
                    continue
                predicate = (right['predicate'] if right['binder'].upper() == left['binder'].upper()
                             else rename_var(right['predicate'], right['binder'], left['binder']))
                merged = copy_node(first)
                combined = Node('bin', left['predicate'].pos, op='AND',
                                l=left['predicate'], r=predicate)
                merged.args = ([first.args[0], first.args[1], combined]
                               if left['explicit'] else [first.args[0], combined])
                next_steps.append(merged)
                i += 2
                changed = True
                continue
            # No rule drops a sort followed by another sort: the sorts are
            # stable, so the first is the second's tie-breaker
            # (rel.sort.then-sort-keeps-the-tie-order), and a rule that removed
            # it changed the value.
            if (second is not None and first.name in ('DISTINCT', 'DEDUPE')
                    and second.name in ('DISTINCT', 'DEDUPE')):
                next_steps.append(first)
                i += 2
                changed = True
                continue
            first_filter = filter_details(first) if first.name == 'FILTER' else None
            if (first_filter is not None and first_filter['valid']
                    and first_filter['predicate'].t == 'bool' and first_filter['predicate'].v
                    and (next_steps or i > 0 or source_is_list(source))):
                changed = True
                i += 1
                continue
            next_steps.append(first)
            i += 1
        current = next_steps
    return current


def _single_relation_name(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.t == 'var':
        return node.name
    if node.t == 'call' and node.args and node.name not in ('LINK', 'LINK_LEFT'):
        return _single_relation_name(node.args[0])
    return None


def collect_pipeline_source_names(node: Node | None) -> list[str]:
    names, seen = [], set()

    def add(name):
        upper = name.upper()
        if upper not in seen:
            seen.add(upper)
            names.append(name)

    def visit(item):
        if item is None:
            return
        if item.t == 'var':
            add(item.name)
            return
        if item.t != 'call' or not item.args:
            return
        if item.name in ('LINK', 'LINK_LEFT'):
            visit(item.args[0])
            visit(item.args[1])
        elif item.name in PIPELINE_OPS:
            visit(item.args[0])

    visit(node)
    return names


def get_table_columns(context: Any, schema: Any, table_name: str) -> set[str] | None:
    cols: set[str] = set()
    if schema and isinstance(schema, dict):
        binding = schema.get(table_name) or schema.get(table_name.lower()) or schema.get(table_name.upper())
        if binding is not None:
            if hasattr(binding, 'columns') and binding.columns:
                cols.update(k.upper() for k in binding.columns.keys())
    if context is not None and table_name:
        from .value import Value
        val = None
        if isinstance(context, Value):
            val = context.get(table_name) or context.get(table_name.upper()) or context.get(table_name.lower())
        elif isinstance(context, dict):
            val = context.get(table_name) or context.get(table_name.upper()) or context.get(table_name.lower())
            if not isinstance(val, Value):
                try:
                    val = Value.from_native(val)
                except Exception:
                    val = None
        if val is not None and not val.is_null():
            sample = None
            if val.is_list and val.storage is not None and val.storage:
                sample = val.storage[0]
            elif val.size() > 0:
                entries = val.entries()
                sample = entries[0][1] if entries else None
            if sample is not None:
                if sample.shape is not None:
                    cols.update(k.upper() for k in sample.shape.keys)
                elif sample.children:
                    cols.update(k.upper() for k in sample.children.keys())
    return cols if cols else None


def pushdown_join_filters(steps: list[Node], context: Any = None, schema: Any = None) -> tuple[list[Node], bool]:
    result = []
    changed = False
    i = 0
    while i < len(steps):
        link = steps[i]
        filter_node = steps[i + 1] if i + 1 < len(steps) else None
        if (filter_node is None or link.name not in ('LINK', 'LINK_LEFT')
                or filter_node.name != 'FILTER'):
            result.append(link)
            i += 1
            continue
        left_source, right_source = link.args[0], link.args[1]
        left_binder = (link.args[2].name if len(link.args) == 5 and link.args[2].t == 'var'
                       and not link.args[2].grouped else _single_relation_name(left_source) or '_1')
        right_binder = (link.args[3].name if len(link.args) == 5 and link.args[3].t == 'var'
                        and not link.args[3].grouped else _single_relation_name(right_source) or '_2')
        left_names = {left_binder.upper(), '_1'}
        right_names = {right_binder.upper(), '_2'}
        left_names.update(name.upper() for name in collect_pipeline_source_names(left_source))
        right_names.update(name.upper() for name in collect_pipeline_source_names(right_source))

        left_cols: set[str] = set()
        for t in collect_pipeline_source_names(left_source):
            c = get_table_columns(context, schema, t)
            if c:
                left_cols.update(c)
        right_cols: set[str] = set()
        for t in collect_pipeline_source_names(right_source):
            c = get_table_columns(context, schema, t)
            if c:
                right_cols.update(c)

        info = filter_details(filter_node)
        if not info['valid']:
            result.append(link)
            i += 1
            continue

        left, right, remaining = [], [], []

        def classify(node):
            has_left = has_right = ambiguous = unknown = False

            def visit(item):
                nonlocal has_left, has_right, ambiguous, unknown
                if item is None:
                    return
                if item.t == 'index':
                    if (item.obj is not None and item.obj.t == 'index'
                            and item.obj.obj is not None and item.obj.obj.t == 'var'
                            and item.obj.obj.name.upper() in (info['binder'].upper(), '_')
                            and item.obj.idx is not None and item.obj.idx.t == 'text'):
                        table = item.obj.idx.v.upper()
                        if table in left_names:
                            has_left = True
                        elif table in right_names:
                            has_right = True
                        else:
                            unknown = True
                        return
                    if item.obj is not None and item.obj.t == 'var' and item.idx.t == 'text':
                        name = item.obj.name.upper()
                        if name in (info['binder'].upper(), '_'):
                            col = item.idx.v.upper()
                            in_left = col in left_cols
                            in_right = col in right_cols
                            if in_left and not in_right:
                                has_left = True
                            elif in_right and not in_left:
                                has_right = True
                            else:
                                ambiguous = True
                        else:
                            unknown = True
                        return
                if item.t == 'var':
                    name = item.name.upper()
                    if name != info['binder'].upper() and name != '_':
                        unknown = True
                for child in item.args:
                    visit(child)
                for child in item.items:
                    visit(child)
                visit(item.l); visit(item.r); visit(item.x)
                visit(item.obj); visit(item.idx)
                visit(item.target); visit(item.value)

            visit(node)
            return has_left, has_right, ambiguous, unknown

        def rewrite(node, target_names):
            if node is None:
                return None
            copy = copy_node(node)
            if copy.t == 'index':
                if (copy.obj is not None and copy.obj.t == 'index'
                        and copy.obj.obj is not None and copy.obj.obj.t == 'var'
                        and copy.obj.obj.name.upper() in (info['binder'].upper(), '_')
                        and copy.obj.idx is not None and copy.obj.idx.t == 'text'
                        and copy.obj.idx.v.upper() in target_names):
                    return Node('index', copy.pos,
                                obj=Node('var', copy.obj.obj.pos, name='_'),
                                idx=copy.idx)
            copy.args = [rewrite(item, target_names) for item in copy.args]
            copy.items = [rewrite(item, target_names) for item in copy.items]
            if copy.l is not None:
                copy.l = rewrite(copy.l, target_names)
            if copy.r is not None:
                copy.r = rewrite(copy.r, target_names)
            if copy.x is not None:
                copy.x = rewrite(copy.x, target_names)
            if copy.obj is not None:
                copy.obj = rewrite(copy.obj, target_names)
            if copy.idx is not None:
                copy.idx = rewrite(copy.idx, target_names)
            if copy.target is not None:
                copy.target = rewrite(copy.target, target_names)
            if copy.value is not None:
                copy.value = rewrite(copy.value, target_names)
            return copy

        for conjunct in split_and(info['predicate']):
            has_left, has_right, ambiguous, unknown = classify(conjunct)
            if has_left and not has_right and not ambiguous and not unknown:
                left.append(rewrite(conjunct, left_names))
            elif (has_right and not has_left and not ambiguous and not unknown
                  and link.name == 'LINK'):
                right.append(rewrite(conjunct, right_names))
            else:
                remaining.append(conjunct)
        if not left and not right:
            result.append(link)
            i += 1
            continue
        if left:
            result.append(call('FILTER', [left_source, combine_and(left, filter_node.pos)], filter_node.pos))
        new_link = link
        if right:
            new_link = copy_node(link)
            new_link.args = list(link.args)
            new_link.args[1] = call('FILTER', [right_source, combine_and(right, filter_node.pos)], filter_node.pos)
        result.append(new_link)
        if remaining:
            new_filter = copy_node(filter_node)
            new_filter.args = ([new_link, filter_node.args[1], combine_and(remaining, filter_node.pos)]
                               if info['explicit'] else [new_link, combine_and(remaining, filter_node.pos)])
            result.append(new_filter)
        changed = True
        i += 2
    return result, changed


def optimize_tree(node: Node | None, physical: bool, depth: int = 1,
                  options: dict[str, Any] | None = None,
                  in_math: bool = False) -> Node | None:
    options = options or {}
    if node is None:
        return None
    # The evaluator/SQL normaliser owns the public depth error and its source
    # position. optimize_root never descends into a tree that reaches the cap;
    # this guard keeps the walk bounded should a rewrite ever deepen one.
    if depth > MAX_DEPTH:
        return node
    if node.t == 'call' and node.name in PIPELINE_OPS:
        source, steps = unwind_pipeline(node)
        optimized_source = optimize_tree(source, physical, depth + 1, options, False)
        optimized_steps = []
        for step in steps:
            copy = copy_node(step)
            copy.args = [copy.args[0], *[
                optimize_tree(item, physical, depth + 1, step_arg_options(step, index, options), False)
                for index, item in enumerate(copy.args[1:], 1)
            ]]
            optimized_steps.append(copy)
        final_steps = logical_steps(optimized_source, optimized_steps, options)
        if physical:
            while True:
                final_steps, pushed = pushdown_join_filters(
                    final_steps,
                    options.get('context'),
                    options.get('schema'),
                )
                if not pushed:
                    break
                final_steps = logical_steps(optimized_source, final_steps, options)
        return build_pipeline(optimized_source, final_steps)

    is_curr_math = is_math_op(node)
    next_in_math = is_curr_math

    copy = copy_node(node)
    copy.args = [optimize_tree(item, physical, depth + 1, options, next_in_math) for item in copy.args]
    copy.items = [optimize_tree(item, physical, depth + 1, options, False) for item in copy.items]
    if copy.l is not None:
        copy.l = optimize_tree(copy.l, physical, depth + 1, options, next_in_math)
    if copy.r is not None:
        copy.r = optimize_tree(copy.r, physical, depth + 1, options, next_in_math)
    if copy.x is not None:
        copy.x = optimize_tree(copy.x, physical, depth + 1, options, next_in_math)
    if copy.obj is not None:
        copy.obj = optimize_tree(copy.obj, physical, depth + 1, options, False)
    if copy.idx is not None:
        copy.idx = optimize_tree(copy.idx, physical, depth + 1, options, False)
    if copy.t == 'assign':
        if copy.value is not None:
            copy.value = optimize_tree(copy.value, physical, depth + 1, options, False)
    elif copy.target is not None:
        copy.target = optimize_tree(copy.target, physical, depth + 1, options, False)
        if copy.value is not None:
            copy.value = optimize_tree(copy.value, physical, depth + 1, options, False)
    elif copy.value is not None:
        copy.value = optimize_tree(copy.value, physical, depth + 1, options, False)

    folded = copy if options.get('foldConstants', True) is False else fold(copy)
    if physical and not in_math and is_math_op(folded):
        plan = compile_math_plan(folded)
        if plan is not None:
            folded.math_plan = plan
    return folded


def exceeds_depth(node: Node | None, depth: int) -> bool:
    """Whether any node of the tree lies past the evaluator's depth cap,
    counted the way the evaluator counts: the root at 1, every child one
    deeper, an assignment's target excluded (the evaluator walks it
    iteratively). The walk stops at the cap, so it is bounded however deep
    the tree is."""
    if node is None:
        return False
    if depth > MAX_DEPTH:
        return True
    nxt = depth + 1
    for item in node.args:
        if exceeds_depth(item, nxt):
            return True
    for item in node.items:
        if exceeds_depth(item, nxt):
            return True
    for child in (node.l, node.r, node.x, node.obj, node.idx):
        if child is not None and exceeds_depth(child, nxt):
            return True
    if node.t != 'assign' and node.target is not None and exceeds_depth(node.target, nxt):
        return True
    if node.value is not None and exceeds_depth(node.value, nxt):
        return True
    return False


def optimize_root(ast: Node, physical: bool, options: dict[str, Any]) -> Node:
    """The evaluator is the depth authority (spec §6.4): a tree that reaches
    the cap is evaluated as written, so it is returned as written. Folding at
    the boundary erased the E_DEPTH the evaluator raises for a chain of 201
    additions (each of them foldable), and a rewrite that lifts a child would
    move it; not rewriting loses nothing, because such a tree either raises or
    keeps its deep part on a branch that is never evaluated."""
    return ast if exceeds_depth(ast, 1) else optimize_tree(ast, physical, 1, options)


def optimize_ast_logical(ast: Node, options: dict[str, Any] | None = None) -> Node:
    return optimize_root(ast, False, options or {})


def optimize_ast_in_memory(ast: Node, context: Any = None, schema: Any = None) -> Node:
    opts: dict[str, Any] = {}
    if context is not None:
        opts['context'] = context
    if schema is not None:
        opts['schema'] = schema
    return optimize_root(ast, True, opts)


def optimize_ast(ast: Node, context: Any = None, schema: Any = None) -> Node:
    return optimize_ast_in_memory(ast, context=context, schema=schema)


# Camel-case aliases keep the API spelling aligned with the JS host.
optimizeAstLogical = optimize_ast_logical
optimizeAstInMemory = optimize_ast_in_memory
optimizeAst = optimize_ast
