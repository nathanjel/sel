"""PY-P4: Node.replaced is dataclasses.replace without the introspection cost, and
must carry every declared field."""
import dataclasses
import sel
from sel.parser import Node


def test_replaced_carries_every_declared_field():
    n = Node('call', None)
    for i, f in enumerate(dataclasses.fields(Node)):
        if f.name not in ('t',):
            setattr(n, f.name, object() if f.name != 'pos' else None)
    c = n.replaced()
    for f in dataclasses.fields(Node):
        if f.name == 'ev':
            # The handler bound for one physical tree (optimizer.bind_handlers)
            # is not carried: a copy may be made into another kind of node.
            assert c.ev is None
            continue
        assert getattr(c, f.name) is getattr(n, f.name), f.name


def test_replaced_overrides_and_shares_like_dataclasses_replace():
    n = sel.compile('MAX(A, 1, 2)').ast
    a = n.replaced(name='MIN')
    b = dataclasses.replace(n, name='MIN')
    for f in dataclasses.fields(Node):
        assert getattr(a, f.name) is getattr(b, f.name) or getattr(a, f.name) == getattr(b, f.name), f.name
    assert a.args is n.args            # unchanged lists are shared, as replace shares them
    fresh = n.replaced(args=list(n.args))
    assert fresh.args is not n.args and fresh.args == n.args
    assert n.name == 'MAX'             # the original is untouched


def test_optimised_trees_are_unchanged_by_the_copy():
    p = sel.compile('IF(D, A * B + C, A - B) > 3 AND NOT (E == 4)')
    want = sel.compile('IF(D, A * B + C, A - B) > 3 AND NOT (E == 4)').run(
        {'A': 2, 'B': 3, 'C': 4, 'D': True, 'E': 5}).dump()
    assert p.run({'A': 2, 'B': 3, 'C': 4, 'D': True, 'E': 5}).dump() == want
    # the logical AST must not be mutated by optimisation (tests/test_runtime_optimizations)
    before = repr(p.ast)
    p.physical_ast()
    assert repr(p.ast) == before
