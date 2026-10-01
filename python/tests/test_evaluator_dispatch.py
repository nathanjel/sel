"""Item 2, P3: eval_node takes a node's evaluator from a table by type (_EVAL)
and, on the optimiser's own copy of the tree, from the node itself (Node.ev,
optimizer.bind_handlers). The caller's AST is never written."""
import runpy
from pathlib import Path

import pytest

from sel import SelError, compile
from sel.errors import Pos
from sel.eval import _EVAL, Context, eval_node
from sel.parser import Node

ROOT = Path(__file__).resolve().parents[2]


def nodes(root):
    out, stack = [], [root]
    while stack:
        n = stack.pop()
        if n is not None:
            out.append(n)
            stack.extend((*n.args, *n.items, n.l, n.r, n.x, n.obj, n.idx, n.target, n.value))
    return out


def test_every_node_type_the_parser_makes_has_an_evaluator():
    conformance = runpy.run_path(str(ROOT / 'python/bin/conformance.py'))
    seen = set()
    for path in sorted((ROOT / 'conformance').glob('*.selt')):
        for case in conformance['parse_selt'](path.read_text(encoding='utf-8'), path.name):
            for source in (case['setup'], case['source']):
                try:
                    seen |= {n.t for n in nodes(compile(source).ast)} if source else set()
                except SelError:
                    pass
    assert len(seen) >= 10 and seen <= set(_EVAL), seen - set(_EVAL)


def test_a_node_of_no_known_type_is_E_SYNTAX_where_it_stands():
    with pytest.raises(SelError) as info:
        eval_node(Node('bogus', Pos(3, 4, 10)), Context())
    assert (info.value.code, info.value.line, info.value.col) == ('E_SYNTAX', 3, 4)


def test_the_physical_tree_carries_its_handlers_and_the_ast_does_not():           # P3b
    from sel.eval import handler_for
    program = compile('A = LIST(1, 2); A[COUNT(A) + 1] = 3; X = A .> FILTER(_ > 1) .> MAP(_ * 2); '
                      'MAP(X, V, V * V + 1 > 4.0 AND TRUE)')
    program.run({})
    assert all(n.ev is None for n in nodes(program.ast))
    physical = nodes(program.physical_ast())
    bound = [n for n in physical if n.ev is not None]
    assert len(bound) > len(physical) // 2
    assert all(n.ev is handler_for(n) for n in bound)


def test_a_tree_past_the_depth_cap_runs_as_written():                              # P3b
    program = compile('1' + ' + 1' * 201)
    assert program.physical_ast() is program.ast
    assert all(n.ev is None for n in nodes(program.ast))
    with pytest.raises(SelError) as info:
        program.run({})
    assert info.value.code == 'E_DEPTH'


def test_a_copy_does_not_carry_the_handler_and_is_still_equal():                  # P3b
    program = compile('A * 2 > 1')
    program.run({'A': 1})
    node = program.physical_ast()
    copy = node.replaced()
    assert node.ev is not None and copy.ev is None
    assert copy == node and repr(copy) == repr(node)
