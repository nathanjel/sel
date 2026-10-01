"""Item 2, P3: eval_node takes a node's evaluator from a table by type (_EVAL)."""
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
