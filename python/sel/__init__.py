"""SEL — a small expression language for validation rules.

One rule file evaluates identically on JavaScript, PHP, C++23, Common Lisp and
Python. Exact decimal arithmetic, no floating point, no truthiness.

    from sel import compile, evaluate, Value, SelError

    program = compile('TOTAL > CREDIT_LIMIT')
    program.dependencies()                      # ['CREDIT_LIMIT', 'TOTAL']
    evaluate('1 + 2').as_text()                 # '3'

Public host interface. See spec/SPEC.md §8.
"""

from __future__ import annotations

from typing import Any

from . import builtins as _builtins   # noqa: F401  registers the function table
from .errors import Pos, SelError, fail
from .errors import MAX_DEPTH
from .eval import Context as _Context, eval_node
from .parser import Node, parse
from .registry import names as _names, binding_form as _binding_form
from .registry import register_function
from ._gc import bulk_allocation as _bulk_allocation
from ._stack import recursion_budget as _recursion_budget
from .value import BIN, BOOL, NONE, TEXT, Value

__all__ = [
    # Context is deliberately absent: it is one evaluation's binder frames and
    # recursion depth, spec/SPEC.md §8 does not list it, nothing outside the
    # evaluator constructs one, and C++ and Lisp never exposed it.
    'compile', 'evaluate', 'Program', 'Value', 'SelError', 'Pos',
    'function_names', 'register_function', 'NONE', 'TEXT', 'BIN', 'BOOL', '__version__',
]

__version__ = '0.10.0'


class Program:
    __slots__ = ('source', 'ast', '_physical', '_physical_of')

    def __init__(self, source: str, ast: Node) -> None:
        self.source = source
        # The parse tree, and the tree every other consumer reads:
        # dependencies(), the SQL translator, the hybrid planner. It is
        # IMMUTABLE once here -- the optimiser and the planner copy on the way
        # down and never write into it (sql/cases/25-hybrid-plans.sqlt asserts
        # so) -- and a caller who builds a Program from an AST of their own is
        # held to the same rule. Reassigning `ast` is fine and drops the cache
        # below; writing into its nodes is not.
        #
        # Two memo fields are the exception, and neither changes what the tree
        # means: an index node's `_cached_slot` (the evaluator's record-slot
        # hint, used only while the record's shape is the one it was taken from,
        # so a hint left by another run is checked, never trusted) and a node's
        # `_not_constant` (SQL stage 1's verdict, one-way and never stale).
        # Each is one whole-attribute store of a value any writer would compute
        # alike, so runs of one Program on several threads can race on them
        # only to write the same answer or a hint the reader re-checks.
        self.ast = ast
        # The physical tree run() evaluates: `ast` after the in-memory
        # optimiser, built on the first run and kept, because the rewrite and
        # the copy it makes cost more than evaluating a small rule does. Keyed
        # by the identity of `ast` so a reassignment is noticed. Private; SQL
        # translation never sees it, since a physical rewrite (join
        # pushdown) is not something a database can be asked to run.
        self._physical: Node | None = None
        self._physical_of: Node | None = None

    def run(self, context: Any = None) -> Value:
        """`context` may be a Value, a plain dict (or list), or omitted/None for an
        empty one. Returns a Value. A Value context is mutated in place by the
        program's assignments; a dict or list is converted, so the caller's own
        object is not. Anything else -- a number, text, bytes, a boolean, and in
        particular a falsy one such as 0 or "" -- is E_BAD_ARG: it is not a
        context, and `run(1.5)` was already refused while `run(0.0)` quietly
        ran against an empty one.
        """
        if isinstance(context, Value):
            root = context
        elif context is None:
            root = Value.from_native({})
        elif isinstance(context, (dict, list, tuple)):
            root = Value.from_native(context)
        else:
            fail('E_BAD_ARG', f'a context is a Value, a dict or a list, not {type(context).__name__}', None)
        # The cyclic collector is paused for the run (sel/_gc.py): a run builds
        # rows, never cycles, and every pass the collector made found nothing.
        with _recursion_budget(), _bulk_allocation():
            return eval_node(self.physical_ast(), _Context(root))

    def physical_ast(self) -> Node:
        """The optimised tree run() evaluates, built once per `ast` and from the
        AST alone -- the other four hosts' rule, which this host broke by keying
        the tree on the context too (SEL-0049): a fresh context per run rebuilt
        it, and its join-filter pushdown read the rows to decide a side."""
        if self._physical_of is not self.ast:
            from .optimizer import optimize_ast
            self._physical = optimize_ast(self.ast)
            self._physical_of = self.ast
        return self._physical

    def dependencies(self) -> list[str]:
        """Every variable the program can read before it has definitely assigned
        it, found statically (spec/SPEC.md 8). Only possible because SEL has no
        dynamic symbol operator; this is what tells a frontend which inputs should
        re-trigger which rule.
        """
        reads: set[str] = set()
        with _recursion_budget():
            _collect(self.ast, frozenset(), frozenset(), reads, 1)
        return sorted(reads)


def _collect(node: Node | None, bound: frozenset, defs: frozenset, reads: set,
             depth: int) -> frozenset:
    """The static walk of the tree, and the third thing in each host that recurses
over it. spec/SPEC.md 6.4 caps the other two -- the parser's nesting and the
evaluator's -- and says why: uncounted recursion over a tree the source can
make arbitrarily deep reaches the host's own stack limit. This walk was
uncounted, and `dependencies()` on a flat chain of about 48,000 operators
raised an uncaught RecursionError, which is not a SEL error at all.

The depth rides as a parameter rather than as a counter with a guard, because
there is nothing to release on the way out -- which is also what lets the five
hosts spell this identically. Capped at the same MAX_DEPTH the evaluator uses
and tripping at the same node, so a program whose dependencies cannot be
computed is exactly a program that could not have been evaluated.

FLOW. The walk goes in evaluation order and carries `defs`, the names definitely
assigned so far, and returns it as it stands afterwards (SPEC 8): a read of a name
that is neither bound nor in `defs` is a dependency. What runs conditionally
(the right side of AND / OR / ?? / ???, the branches of IF and COND, an aggregate's
per-element expressions) contributes to `defs` only where every path assigns it.
    """
    if node is None:
        return defs
    if depth > MAX_DEPTH:
        fail('E_DEPTH', 'expression nested too deeply', node.pos)
    t = node.t
    d1 = depth + 1

    if t == 'var':
        if node.name not in bound and node.name not in defs:
            reads.add(node.name)
        return defs

    if t == 'assign':
        target = node.target
        idxs = []
        while target.t == 'index':
            idxs.append(target.idx)
            target = target.obj
        # Index expressions run in source order, before the right side.
        for idx in reversed(idxs):
            defs = _collect(idx, bound, defs, reads, d1)
        # `A = x` defines A and `A[k] = x` creates it, so neither reads it;
        # `A op= x` and `A[k] op= x` do.
        if node.op != '=':
            if target.name not in bound and target.name not in defs:
                reads.add(target.name)
        defs = _collect(node.value, bound, defs, reads, d1)
        return defs | {target.name}

    if t == 'call':
        name = node.name or ''              # a call's name is the canonical one
        if name == 'IF' and len(node.args) == 3:
            defs = _collect(node.args[0], bound, defs, reads, d1)
            a = _collect(node.args[1], bound, defs, reads, d1)
            b = _collect(node.args[2], bound, defs, reads, d1)
            return a & b
        if name == 'COND' and len(node.args) % 2 == 1:
            args = node.args
            paths = []
            state = defs
            for k in range(0, len(args) - 1, 2):
                state = _collect(args[k], bound, state, reads, d1)
                paths.append(_collect(args[k + 1], bound, state, reads, d1))
            paths.append(_collect(args[-1], bound, state, reads, d1))
            out = paths[0]
            for extra in paths[1:]:
                out = out & extra
            return out
        # Which arguments run inside the binder, and what they see, is decided
        # once, by binding_form() over the manifest's forms (spec/builtins.md).
        # No form -- a strict function, or a count the evaluator would refuse
        # -- and every argument is read where the call stands.
        form = _binding_form(node.name or '', node.args, node.spec)
        if form is None:
            for i, a in enumerate(node.args):
                # The first argument always runs; COALESCE's later ones and GET/PATH's
                # default may not, so nothing they assign is definite afterwards.
                if (name == 'COALESCE' and i > 0) or (name in ('GET', 'PATH') and i > 1):
                    _collect(a, bound, defs, reads, d1)
                else:
                    defs = _collect(a, bound, defs, reads, d1)
            return defs
        scopes, binds = form
        inner = None
        for i, arg in enumerate(node.args):
            scope = scopes[i]
            if scope == 'binder':
                continue
            if scope == 'inner':
                if inner is None:
                    inner = bound | frozenset(binds)
                # Runs once per element, possibly never: what it assigns is
                # not definite outside the call.
                _collect(arg, inner, defs, reads, d1)
            else:
                defs = _collect(arg, bound, defs, reads, d1)
        return defs

    if t in ('seq', 'list'):
        for item in node.items:
            defs = _collect(item, bound, defs, reads, d1)
        return defs

    if t == 'index':
        defs = _collect(node.obj, bound, defs, reads, d1)
        return _collect(node.idx, bound, defs, reads, d1)

    if t == 'bin':
        defs = _collect(node.l, bound, defs, reads, d1)
        if node.op in ('AND', 'OR', '??', '???'):
            _collect(node.r, bound, defs, reads, d1)
            return defs
        return _collect(node.r, bound, defs, reads, d1)

    if t == 'un':
        return _collect(node.x, bound, defs, reads, d1)

    return defs


def compile(source: str) -> Program:   # noqa: A001 - mirrors compile() in every host
    """Parse and check `source`. Raises SelError on a syntax error, an unknown
    function name or a wrong argument count — all three are compile time.
    """
    if not isinstance(source, str):
        # Not source at all (spec/SPEC.md 8): a bytes, a number, None. A caller's
        # mistake, reported as ours rather than as whatever the lexer chokes on.
        fail('E_BAD_ARG', f'compile takes program text, not {type(source).__name__}')
    with _recursion_budget():
        return Program(source, parse(source))


def evaluate(source: str, context: Any = None) -> Value:
    return compile(source).run(context)


def function_names() -> list[str]:
    return _names()
