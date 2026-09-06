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
from .eval import MAX_DEPTH, Context, eval_node
from .parser import Node, parse
from .registry import names as _names
from .value import BIN, BOOL, NONE, TEXT, Value

__all__ = [
    'compile', 'evaluate', 'Program', 'Value', 'SelError', 'Context', 'Pos',
    'function_names', 'NONE', 'TEXT', 'BIN', 'BOOL', '__version__',
]

__version__ = '0.3.0'


class Program:
    __slots__ = ('source', 'ast')

    def __init__(self, source: str, ast: Node) -> None:
        self.source = source
        self.ast = ast

    def run(self, context: Any = None) -> Value:
        """`context` may be a Value, a plain dict, or omitted. Returns a Value;
        the context is mutated in place by any assignments the program performs.
        """
        root = context if isinstance(context, Value) else Value.from_native(context or {})
        return eval_node(self.ast, Context(root))

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
        # An aggregate's three-argument form binds its second argument as a name
        # for the duration of the third.
        if node.spec and node.spec.binds and len(node.args) == 3 and node.args[1].t == 'var':
            _collect(node.args[0], bound, reads, assigned, depth + 1)
            inner = bound | {node.args[1].name, '_K'}
            _collect(node.args[2], inner, reads, assigned, depth + 1)
            return
        if node.spec and node.spec.binds and len(node.args) == 2:
            _collect(node.args[0], bound, reads, assigned, depth + 1)
            inner = bound | {'_', '_K'}
            _collect(node.args[1], inner, reads, assigned, depth + 1)
            return
        for a in node.args:
            _collect(a, bound, reads, assigned, depth + 1)
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
