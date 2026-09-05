"""Stage 1: turn a small, well-behaved class of SEL programs into one expression,
and refuse the rest.

SEL is an expression language, but it has assignment and ``;``, and SQL has
neither. This is the restrictive step: a helper variable is inlined as the
expression it held, and anything that cannot be is refused with a position.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from ..errors import Pos
from ..parser import Node
from . import constants as _constants
from .errors import refuse


class CList:
    """A keyed list, built by indexed assignment and by nothing else.

    Distinct from a ``list`` node because it must NOT be flattened: assignment
    stores a list as a child rather than contributing its children (spec §5.9
    applies to ``,`` and not to ``=``), so ``R[1] = (1, 2); R[2] = (3, 4)`` is
    two pairs and not four scalars, and this is the only node that can say so.
    It carries the real keys too, so ``R["a"] = 1`` gives ``_K`` of ``"a"``.

    A class of its own rather than a field added to ``Node``: the parser never
    produces one, the evaluator has never seen one, and this is the SQL layer's
    node, not the language's. It duck-types as a Node -- ``.t`` and ``.pos`` --
    which is all any consumer here reads.
    """

    __slots__ = ('t', 'pos', 'entries')

    def __init__(self, pos: Pos, entries: list[tuple[str, Any]] | None = None) -> None:
        self.t = 'clist'
        self.pos = pos
        self.entries: list[tuple[str, Any]] = entries if entries is not None else []


def run(ast: Node, const_names: dict[str, bool] | None = None, ctx=None) -> Any:
    stmts = list(ast.items) if ast.t == 'seq' else [ast]
    result = stmts.pop()
    defs: dict[str, Any] = {}                      # NAME => node

    for s in stmts:
        _record(s, defs, const_names or {}, ctx)
    return _substitute(result, defs, [])


def _record(s: Node, defs: dict[str, Any],
            const_names: dict[str, bool], ctx) -> None:
    """Fold one leading statement into ``defs``, or refuse it."""
    if s.t != 'assign':
        refuse('E_SQL_ASSIGN',
               'only assignments may come before the result expression; this '
               'computes a value nothing reads, which SQL has nowhere to put',
               s.pos)
    if s.op != '=':
        refuse('E_SQL_ASSIGN',
               f'{s.op} reads its own target before writing it, and SQL has nowhere '
               'to put the write; use = and a fresh name', s.pos)

    # The target is a bare name, or a name indexed by constant keys.
    keys: list[str] = []
    t = s.target
    while t.t == 'index':
        k = _constant_key(t.idx)
        if k is None:
            refuse('E_SQL_ASSIGN',
                   'an assignment target may only be indexed by a constant here, '
                   'because the shape has to be known before the query runs',
                   t.idx.pos)
        keys.insert(0, k)
        t = t.obj
    if t.t != 'var':
        refuse('E_SQL_ASSIGN', 'assignment target is not a variable', s.pos)
    name = t.name

    value = _substitute(s.value, defs, [])

    # Validated here, and only here, because after this the subtree may be gone:
    # a definition nothing reads is dropped, so `A = 1 / 0; TRUE` translated to
    # `TRUE` and every server answered TRUE where SEL raises E_DIV_ZERO. An
    # indexed assignment builds a `clist`, which the constant test refuses to
    # walk and COUNT/HAS never render, so `R[1] = 1 / 0; COUNT(R)` was `1`.
    # §11.4's fourth bullet says a constant subtree is checked wherever it
    # appears; these were the two places it did not appear by the time anything
    # looked.
    if _constants.is_constant(value, const_names):
        _constants.validate(value, ctx)

    if not keys:
        if name in defs:
            refuse('E_SQL_ASSIGN',
                   f'{name} is assigned more than once; SQL has no notion of a '
                   'variable changing, so each name may be written once', s.pos)
        defs[name] = value
        return

    if len(keys) > 1:
        refuse('E_SQL_ASSIGN',
               'only one level of indexed assignment can be folded into a list here',
               s.pos)
    key = keys[0]
    if name not in defs:
        defs[name] = CList(s.pos)
    if defs[name].t != 'clist':
        refuse('E_SQL_ASSIGN',
               f'{name} is assigned both as a whole and by index; use one or the other',
               s.pos)
    for existing, _v in defs[name].entries:
        if existing == key:
            refuse('E_SQL_ASSIGN', f'{name}[{key}] is assigned more than once', s.pos)
    defs[name].entries.append((key, value))


def _constant_key(idx: Node) -> str | None:
    """The literal key an index expression names, or None when it is not one."""
    if idx.t in ('num', 'text'):
        return str(idx.v)
    return None


def _substitute(node: Any, defs: dict[str, Any], bound: list[str]) -> Any:
    """Replace every read of a defined name with the node it was assigned.

    The substituted subtree keeps its original ``pos``, so an error inside an
    inlined expression still points at where the author wrote it rather than at
    the place it was used.

    Every branch that changes a child COPIES the node first. PHP gets this free,
    because its arrays are values and `$node['x'] = …` mutates a copy; a Python
    Node is a mutable object shared with the caller's AST, and rewriting one in
    place would leave the program permanently substituted -- visible to the next
    translation of the same Program, and to the evaluator.
    """
    t = node.t
    if t == 'var':
        if node.name in bound:
            return node
        return defs.get(node.name, node)

    if t in ('num', 'text', 'bool'):
        return node

    if t == 'assign':
        refuse('E_SQL_ASSIGN',
               'an assignment here would have to happen while the query runs, and a '
               'SQL expression cannot assign', node.pos)

    if t == 'seq':
        refuse('E_SQL_ASSIGN',
               'a sequence here would evaluate and discard a value, which a SQL '
               'expression cannot do', node.pos)

    if t == 'un':
        return dataclasses.replace(node, x=_substitute(node.x, defs, bound))

    if t == 'bin':
        return dataclasses.replace(node,
                                   l=_substitute(node.l, defs, bound),
                                   r=_substitute(node.r, defs, bound))

    if t == 'index':
        return dataclasses.replace(node,
                                   obj=_substitute(node.obj, defs, bound),
                                   idx=_substitute(node.idx, defs, bound))

    if t == 'list':
        return dataclasses.replace(node, items=_flatten(node.items, defs, bound))

    if t == 'clist':
        return CList(node.pos, [(k, _substitute(v, defs, bound))
                                for k, v in node.entries])

    if t == 'call':
        inner = list(bound)
        binds = node.spec is not None and node.spec.binds
        if binds:
            n = len(node.args)
            inner.append('_K')
            inner.append(node.args[1].name
                         if n == 3 and node.args[1].t == 'var' else '_')
        args = []
        for i, arg in enumerate(node.args):
            # An aggregate's binder argument is a name, not a read of one.
            if binds and i == 1 and len(node.args) == 3 and arg.t == 'var':
                args.append(arg)
                continue
            args.append(_substitute(arg, defs, bound if i == 0 else inner))
        return dataclasses.replace(node, args=args)

    return node


def _flatten(items: list[Any], defs: dict[str, Any], bound: list[str]) -> list[Any]:
    """Build a ``,`` list, flattening per spec §5.9: an operand with children and
    no scalar of its own contributes each of its children, and the keys are
    renumbered from 1.

    Both node kinds contribute, and both contribute exactly one level. Verified
    against the evaluator, which is the arbiter here::

        ((1, 2), 3)                       -> three scalars
        R[1] = (1, 2); R[2] = (3, 4); (R, 5)
                                          -> (1,2), (3,4), 5 -- three elements,
                                             two of which are still lists

    Parenthesising changes nothing: the ``grouped`` flag decides whether a call
    sees one argument or several, not whether ``,`` flattens.

    What ``clist`` protects is not this. It is that indexed assignment builds
    nesting out of ``R[1] = …; R[2] = …``, and a ``list`` node would have been
    renumbered flat by the time an aggregate iterated it. A ``clist`` reaches an
    aggregate through a bare variable reference, never through a ``,``, so the
    two rules never meet.
    """
    out: list[Any] = []
    for item in items:
        s = _substitute(item, defs, bound)
        if s.t == 'list':
            out.extend(s.items)
            continue
        if s.t == 'clist':
            out.extend(v for _k, v in s.entries)
            continue
        out.append(s)
    return out
