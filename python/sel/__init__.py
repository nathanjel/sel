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
from .eval import MAX_DEPTH, Context as _Context, eval_node
from .parser import Node, parse
from .registry import names as _names, binding_form as _binding_form
from .value import BIN, BOOL, NONE, TEXT, Value

__all__ = [
    # Context is deliberately absent: it is one evaluation's binder frames and
    # recursion depth, spec/SPEC.md §8 does not list it, nothing outside the
    # evaluator constructs one, and C++ and Lisp never exposed it.
    'compile', 'evaluate', 'Program', 'Value', 'SelError', 'Pos',
    'function_names', 'NONE', 'TEXT', 'BIN', 'BOOL', '__version__',
]

__version__ = '0.7.4'


class Program:
    __slots__ = ('source', 'ast', '_physical', '_physical_of', '_physical_context')

    def __init__(self, source: str, ast: Node) -> None:
        self.source = source
        # The parse tree, and the tree every other consumer reads:
        # dependencies(), the SQL translator, the hybrid planner. It is
        # IMMUTABLE once here -- the optimiser and the planner copy on the way
        # down and never write into it (sql/cases/25-hybrid-plans.sqlt asserts
        # so) -- and a caller who builds a Program from an AST of their own is
        # held to the same rule. Reassigning `ast` is fine and drops the cache
        # below; writing into its nodes is not.
        self.ast = ast
        # The physical tree run() evaluates: `ast` after the in-memory
        # optimiser, built on the first run and kept, because the rewrite and
        # the copy it makes cost more than evaluating a small rule does. Keyed
        # by the identity of `ast` so a reassignment is noticed. Private; SQL
        # translation never sees it, since a physical rewrite (join
        # pushdown) is not something a database can be asked to run.
        self._physical: Node | None = None
        self._physical_of: Node | None = None
        self._physical_context: Any = None

    def run(self, context: Any = None) -> Value:
        """`context` may be a Value, a plain dict, or omitted. Returns a Value;
        the context is mutated in place by any assignments the program performs.
        """
        root = context if isinstance(context, Value) else Value.from_native(context or {})
        return eval_node(self.physical_ast(root), _Context(root))

    def physical_ast(self, context: Any = None) -> Node:
        """The optimised tree run() evaluates, built once per `ast`."""
        if (self._physical_of is not self.ast
                or (context is not None and self._physical_context is not context)):
            from .optimizer import optimize_ast
            self._physical = optimize_ast(self.ast, context=context)
            self._physical_of = self.ast
            self._physical_context = context
        return self._physical

    def dependencies(self) -> list[str]:
        """Every variable the program reads without having assigned it first,
        found statically. Only possible because SEL has no dynamic symbol
        operator; this is what tells a frontend which inputs should re-trigger
        which rule.
        """
        reads: set[str] = set()
        assigned: set[str] = set()
        _collect(self.ast, frozenset(), reads, assigned, 1)
        return sorted(n for n in reads if n not in assigned)


def _collect(node: Node | None, bound: frozenset, reads: set, assigned: set,
             depth: int) -> None:
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
    """
    if node is None:
        return
    if depth > MAX_DEPTH:
        fail('E_DEPTH', 'expression nested too deeply', node.pos)
    t = node.t

    if t == 'var':
        if node.name not in bound:
            reads.add(node.name)
        return

    if t == 'assign':
        target = node.target
        while target.t == 'index':
            _collect(target.idx, bound, reads, assigned, depth + 1)
            target = target.obj
        # `A = x` defines A; `A[k] = x` and `A += x` also read it.
        if node.target.t != 'var' or node.op != '=':
            if target.name not in bound:
                reads.add(target.name)
        assigned.add(target.name)
        _collect(node.value, bound, reads, assigned, depth + 1)
        return

    if t == 'call':
        # Which arguments run inside the binder, and what they see, is decided
        # once, by binding_form() over the manifest's forms (spec/builtins.md).
        # No form -- a strict function, or a count the evaluator would refuse
        # -- and every argument is read where the call stands.
        form = _binding_form(node.name or '', node.args, node.spec)
        if form is None:
            for a in node.args:
                _collect(a, bound, reads, assigned, depth + 1)
            return
        scopes, binds = form
        inner = None
        for i, arg in enumerate(node.args):
            scope = scopes[i]
            if scope == 'binder':
                continue
            if scope == 'inner':
                if inner is None:
                    inner = bound | frozenset(binds)
                _collect(arg, inner, reads, assigned, depth + 1)
            else:
                _collect(arg, bound, reads, assigned, depth + 1)
        return

    if t in ('seq', 'list'):
        for item in node.items:
            _collect(item, bound, reads, assigned, depth + 1)
        return

    if t == 'index':
        _collect(node.obj, bound, reads, assigned, depth + 1)
        _collect(node.idx, bound, reads, assigned, depth + 1)
        return

    if t == 'bin':
        _collect(node.l, bound, reads, assigned, depth + 1)
        _collect(node.r, bound, reads, assigned, depth + 1)
        return

    if t == 'un':
        _collect(node.x, bound, reads, assigned, depth + 1)
        return


def compile(source: str) -> Program:   # noqa: A001 - mirrors compile() in every host
    """Parse and check `source`. Raises SelError on a syntax error, an unknown
    function name or a wrong argument count — all three are compile time.
    """
    return Program(source, parse(source))


def evaluate(source: str, context: Any = None) -> Value:
    return compile(source).run(context)


def function_names() -> list[str]:
    return _names()
