"""Engine-independent logical and physical pipeline rewrites.

The AST is treated as immutable by callers.  Rewrites copy nodes as they are
visited, which lets the same optimizer feed the in-memory evaluator and the SQL
translator without changing a compiled program behind the host's back.
"""

from __future__ import annotations

from typing import Any

from . import decimal as D
from .errors import MAX_DEPTH
from .eval import bytes_compare
from .lexer import ascii_upper
from .math_plan import compile_math_plan, is_math_op
from .parser import Node
from .registry import is_host_function, lookup
from .utf8 import encode_utf8


PIPELINE_OPS = frozenset({
    'FILTER', 'BUCKET', 'SELECT_COLS', 'MAP', 'DISTINCT', 'DEDUPE',
    'TAKE', 'DROP', 'SORT', 'SORT_DESC', 'SORT_BY', 'TOP', 'TOP_DESC', 'TOP_BY',
    'LINK', 'LINK_LEFT',
})


def copy_node(node: Node | None) -> Node | None:
    if node is None:
        return None
    return node.replaced(args=list(node.args), items=list(node.items))


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


def literal_num(value: str, pos, dec=None) -> Node:
    """A number literal node. DEC is the decoded Dec of `value` when the caller has
    it (every fold does: it just computed it), so the evaluator's 'num' branch
    reads it instead of re-parsing the text on every evaluation -- a folded node
    used to carry none -- and a fold over a folded operand needs no re-parse."""
    return Node('num', pos, v=value, dec=dec)


def literal_dec(node: Node) -> D.Dec:
    """The Dec of a 'num' node: the one the parser (or an earlier fold) decoded, or
    a parse of its text."""
    return node.dec if node.dec is not None else D.parse(node.v, node.pos)


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
    return child.replaced(pos=pos)


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
                negated = D.negate(literal_dec(node.x))
                return literal_num(D.format(negated), node.pos, negated)
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
                left = literal_dec(node.l)
                right = literal_dec(node.r)
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
                return literal_num(D.format(result), node.pos, result)
            except Exception:
                return node
        if (node.l.t == 'num' and node.r.t == 'num'
                and node.op in ('==', '!=', '<', '<=', '>', '>=')):
            try:
                c = D.cmp(literal_dec(node.l), literal_dec(node.r))
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
                and any(ascii_upper(name) == ascii_upper(item.obj.name)
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


# The steps that only READ the elements they are handed and copy whatever they
# keep (SPEC 3.4: MAP copies what it collects). A FILTER in front of one need not
# copy its kept elements: they are read once and MAP's own copy is the copy the
# contract asks for (PY-REG-1). Only a pipeline step can follow a FILTER this way;
# SUM, ALL and ANY take their source as an argument and never reach this test, and
# BUCKET, SELECT_COLS and the sorts hand the elements on or build from them, so
# they are not here.
_READ_ONLY_CONSUMERS = ('MAP',)


def body_only_reads(node: Node | None) -> bool:
    """Whether evaluating NODE can change nothing it reaches: no assignment, and
    no call to an application's own function (which may do anything to a value
    it is handed). Iterative: a body is as deep as the source is long."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        t = n.t
        if t == 'assign':
            return False
        if t == 'call':
            if is_host_function(n.name or ''):
                return False
            stack.extend(n.args)
        elif t == 'index':
            stack.append(n.obj)
            stack.append(n.idx)
        elif t == 'bin':
            stack.append(n.l)
            stack.append(n.r)
        elif t == 'un':
            stack.append(n.x)
        elif t in ('seq', 'list'):
            stack.extend(n.items)
    return True


def adopts_elements(step: Node | None) -> bool:
    """Whether the step after a FILTER only reads the elements FILTER keeps."""
    return (step is not None and step.t == 'call' and step.name in _READ_ONLY_CONSUMERS
            and all(body_only_reads(a) for a in step.args[1:]))


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


# Whether evaluating NODE for one row can raise -- conservatively: a rewrite that
# moves a FILTER in front of a step, runs a step on fewer rows, or fuses two
# FILTERs changes which rows reach what, so it may only pass over expressions
# that cannot raise on any of them (spec §7.3; review 2026-09-25 SEM-07/SEM-08).
# Literals, _K and the binder itself never raise. On the logical path the rows
# are a bound relation's, which always carry their typed columns, so a field
# read through the binder cannot raise either, nor a comparison, AND/OR/NOT or
# + - * over such reads; `/` and `%`, calls and anything else may. The
# in-memory path has no schema.
_SAFE_LOGICAL_OPS = frozenset(('==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
                               'AND', 'OR', '+', '-', '*'))


def cannot_raise(node, binder: str, logical: bool) -> bool:
    if node is None:
        return True
    t = node.t
    if t in ('num', 'text', 'bool', 'null'):
        return True
    if t == 'var':
        name = node.name.upper()
        return name == '_K' or name == binder.upper()
    if t == 'index':
        return (logical and node.obj is not None and node.obj.t == 'var'
                and node.obj.name.upper() == binder.upper()
                and node.idx is not None and node.idx.t == 'text')
    if t == 'bin':
        return (logical and node.op in _SAFE_LOGICAL_OPS
                and cannot_raise(node.l, binder, logical) and cannot_raise(node.r, binder, logical))
    if t == 'un':
        return logical and node.op == 'NOT' and cannot_raise(node.x, binder, logical)
    return False


_BOOL_OPS = frozenset(('==', '!=', '<', '<=', '>', '>=', '$==', '$!=', '$<', '$<=', '$>', '$>=',
                       'AND', 'OR'))


def predicate_cannot_raise(node, binder: str, logical: bool) -> bool:
    """`cannot_raise` for a FILTER predicate, which must also come out BOOL: a
    bare variable, `_K`, a number, a text or NULL never raises when read but is
    E_NOT_BOOL as a predicate, so a fusion that moved it before an earlier
    predicate's later rows changed which error came first (PHP-C11)."""
    if node is None:
        return True
    if node.t == 'bool':
        return True
    if node.t == 'bin' and node.op in _BOOL_OPS:
        return cannot_raise(node, binder, logical)
    if node.t == 'un' and node.op == 'NOT':
        return cannot_raise(node, binder, logical)
    return False


def map_cannot_raise(step: Node, logical: bool) -> bool:
    """Every field a MAP computes (or its whole body) cannot raise."""
    details = map_details(step)
    body = details['body']
    if body is not None and body.t == 'call' and body.name == 'RECORD':
        return all((arg.t == 'text') if i % 2 == 0 else cannot_raise(arg, details['binder'], logical)
                   for i, arg in enumerate(body.args))
    return cannot_raise(body, details['binder'], logical)


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
        value = node.dec if node.dec is not None else D.parse(node.v)
        if value is None or not D.is_integer(value):
            return None
        result = D.to_safe_int(value)
        return result if result >= 0 else None
    except Exception:
        return None


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


def fusable_take_count(node: Node | None) -> bool:
    n = numeric_literal(node)
    return n is not None and n >= 1


def logical_steps(source: Node | None, steps: list[Node],
                  options: dict[str, Any] | None = None) -> list[Node]:
    options = options or {}
    logical = bool(options.get('logical', False))
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
            # Fused only for a numeric literal count of at least 1 (SPEC 6.2):
            # the fused TOP evaluates its count before the keys run, and a
            # count of 0 or less returns before looking at them, so anything
            # that is not a literal >= 1 has to run as SORT then TAKE.
            if (second is not None and second.name == 'TAKE'
                    and first.name in ('SORT', 'SORT_DESC', 'SORT_BY')
                    and len(second.args) == 2
                    and fusable_take_count(second.args[1])):
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
                        and keys_renumbered_by(current[i + 2] if i + 2 < len(current) else None)
                        and map_cannot_raise(first, logical)):
                    next_steps.extend((second, first))
                    i += 2
                    changed = True
                    continue
            if (second is not None and first.name in ('SORT', 'SORT_DESC', 'SORT_BY')
                    and second.name == 'FILTER' and not step_reads_key(second)
                    and keys_renumbered_by(current[i + 2] if i + 2 < len(current) else None)
                    and cannot_raise(sort_details(first)['key'], sort_details(first)['binder'] or '_', logical)
                    and (logical or predicate_cannot_raise(filter_details(second)['predicate'],
                                                           filter_details(second)['binder'], False))):
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
                        and not reads_row_or_key(details['key'], details['binder'] or '_')
                        and map_cannot_raise(first, logical)
                        and cannot_raise(details['key'], details['binder'] or '_', logical)):
                    next_steps.extend((second, first))
                    i += 2
                    changed = True
                    continue
            if (options.get('fuseFilters', True) is not False
                    and second is not None and first.name == 'FILTER' and second.name == 'FILTER'):
                left, right = filter_details(first), filter_details(second)
                # Fused, the second predicate runs on a row before the first has
                # seen the rows after it: only one that cannot raise.
                if not left['valid'] or not right['valid'] or not predicate_cannot_raise(right['predicate'], right['binder'], logical):
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
                    and (next_steps or i > 0 or source_is_list(source))
                    # In memory a FILTER(x, TRUE) that would be the only step is
                    # kept: dropping it leaves the source standing for the call,
                    # and the source reports at its own position.
                    and (logical or next_steps or i + 1 < len(current))):
                changed = True
                i += 1
                continue
            next_steps.append(first)
            i += 1
        current = next_steps
    return current


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
        final_steps = logical_steps(optimized_source, optimized_steps, {**options, 'logical': not physical})
        if physical:
            # A tree fact the evaluator's join pre-filter needs (SEL-0050):
            # whether anything can see the keys a FILTER's result carries. A
            # following step that renumbers without reading `_K` hides them
            # (keys_renumbered_by, the same notion the logical rewrites use);
            # the end of the pipeline or another FILTER does not. Stamped on
            # the body node of the physical copy, never on the caller's AST.
            for index, step in enumerate(final_steps):
                if step.name == 'FILTER':
                    following = final_steps[index + 1] if index + 1 < len(final_steps) else None
                    step.args[-1].keys_unobserved = keys_renumbered_by(following)
                    step.adopt_items = (
                        body_only_reads(step)
                        and adopts_elements(following)
                    )
        root = build_pipeline(optimized_source, final_steps)
        if physical and final_steps:
            # A fused or dropped step leaves a node standing for the whole call:
            # it reports where the call it replaced would have (SPEC 6.3, the
            # node that actually failed), not where its first stage began.
            root.pos = node.pos
        return root

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
    # An assignment's target (only assign nodes have one) is walked
    # iteratively by the evaluator, so it is not charged here.
    if copy.value is not None:
        copy.value = optimize_tree(copy.value, physical, depth + 1, options, False)

    folded = copy if options.get('foldConstants', True) is False else fold(copy)
    if physical and folded.t == 'bin' and folded.op == 'IN' and _literal_list(folded.r):
        folded.const_value = _build_constant(folded.r)
    if physical and not in_math and is_math_op(folded):
        plan = compile_math_plan(folded)
        if plan is not None:
            folded.math_plan = plan
    return folded


class ConstantList:
    """The Value of a literal list beside, when every element is text or a number,
    the set of their texts: EQL between two plain text values is equality of their
    texts, so `x IN list` with a plain text x is a set lookup (see eval._eval_binary).
    Immutable once built and private to the IN node that holds it."""
    __slots__ = ('value', 'texts')

    def __init__(self, value) -> None:
        self.value = value
        texts = None
        if value.kind == 'NONE' and value.size() > 0:
            elements = [child for _, child in value.entries()]
            if all(c.kind == 'TEXT' and c.size() == 0 for c in elements):
                texts = frozenset(c.scalar for c in elements)
        self.texts = texts


def _literal_list(node: Node | None) -> bool:
    """`("a", "b")` or `LIST("a", "b")`: a list of nothing but literals. Evaluating
    it reads no variable, runs no code and can raise nothing (the tree is within
    the depth cap, optimize_root checked), so its Value is a constant."""
    if node is None:
        return False
    if node.t == 'list':
        elements = node.items
    elif node.t == 'call' and node.name == 'LIST':
        elements = node.args
    else:
        return False
    return bool(elements) and all(is_literal(e) for e in elements)


def _build_constant(node: Node) -> Any:
    """The list's Value, built with the evaluator itself so it is what a per-row
    evaluation would have produced; None (so the row path runs as before) should
    it refuse."""
    from .eval import Context, eval_node
    from .errors import SelError
    from .value import Value
    try:
        value = eval_node(node, Context(Value.from_native({})))
    except SelError:
        return None
    return ConstantList(value)


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


def optimize_ast_in_memory(ast: Node) -> Node:
    # The physical tree is a function of the AST alone, in every host: no
    # context, no schema. It is built once per program and kept (SEL-0049).
    physical = optimize_root(ast, True, {})
    if physical is not ast:
        bind_handlers(physical, ast)
    return physical


def _children(n: Node) -> tuple:
    return (*n.args, *n.items, n.l, n.r, n.x, n.obj, n.idx, n.target, n.value)


def bind_handlers(physical: Node, ast: Node) -> None:
    """Stamps every node the physical tree owns with the function eval_node runs
    it with (eval.handler_for; item 2, P3). A node it shares with the caller's
    AST -- an assignment's target, a pipeline step's placeholder -- is never
    written, and neither is anything below it: it takes the generic path."""
    from .eval import handler_for
    shared: set[int] = set()
    stack: list = [ast]
    while stack:
        n = stack.pop()
        if n is not None and id(n) not in shared:
            shared.add(id(n))
            stack.extend(_children(n))
    stack = [physical]
    while stack:
        n = stack.pop()
        if n is not None and id(n) not in shared and n.ev is None:
            n.ev = handler_for(n)
            stack.extend(_children(n))


def optimize_ast(ast: Node) -> Node:
    return optimize_ast_in_memory(ast)
