"""eval_node takes a node's evaluator from a table by type (_EVAL)
and, on the optimiser's own copy of the tree, from the node itself (Node.ev,
optimizer.bind_handlers). The caller's AST is never written."""
import runpy
import sys
from pathlib import Path

import pytest

from sel import SelError, compile
from sel.errors import Pos
from sel.eval import _EVAL, Context, eval_node
from sel.parser import Node

ROOT = Path(__file__).resolve().parents[2]


def load_conformance_runner():
    # The runner imports its sibling `_harness` by bare name, as a script does;
    # its read_text (bytes, no newline translation) is in the returned globals.
    bin_dir = str(ROOT / 'python/bin')
    if bin_dir not in sys.path:
        sys.path.append(bin_dir)
    return runpy.run_path(str(ROOT / 'python/bin/conformance.py'))


def nodes(root):
    out, stack = [], [root]
    while stack:
        n = stack.pop()
        if n is not None:
            out.append(n)
            stack.extend((*n.args, *n.items, n.l, n.r, n.x, n.obj, n.idx, n.target, n.value))
    return out


def test_every_node_type_the_parser_makes_has_an_evaluator():
    conformance = load_conformance_runner()
    seen = set()
    for path in sorted((ROOT / 'conformance').glob('*.selt')):
        for case in conformance['parse_selt'](conformance['read_text'](str(path)), path.name):
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


def _observe(run):
    try:
        return ('value', run().dump())
    except SelError as e:
        return (e.code, e.line, e.col)


def test_a_record_with_literal_keys_runs_on_its_own_evaluator():
    from sel.builtins.structure import _eval_record
    from sel.eval import _eval_call
    program = compile('X = 1; LIST(RECORD("a", X, "b", 2), RECORD("a" & "", 1), RECORD("a", 1, "a", 2))')
    program.run({})
    calls = {n.pos.col: n.ev for n in nodes(program.physical_ast())
             if n.t == 'call' and n.name == 'RECORD'}
    assert sorted(calls) == [13, 37, 58]
    assert calls[13] is _eval_record          # distinct literal keys
    assert calls[37] is _eval_call            # a computed key
    assert calls[58] is _eval_call            # a repeated key


@pytest.mark.parametrize('src', [
    'RECORD("a", "x")',
    'RECORD("a", "x", "b", RECORD("c", "z"))',
    'RECORD("a", ("x" & "w"), "b", 1 + 2)',
])
def test_the_record_evaluator_meets_the_depth_cap_where_the_call_does(src):
    # The keys are not evaluated, so the E_DEPTH the first one would raise at
    # the cap is raised for it, at its position. A tree that deep runs as
    # written (above), so the evaluator is met by starting the count high.
    from sel import Value
    from sel.builtins.structure import _eval_record
    from sel.errors import MAX_DEPTH
    program = compile(src)
    physical = program.physical_ast()
    assert physical.ev is _eval_record
    for start in range(MAX_DEPTH - 3, MAX_DEPTH + 1):
        observed = []
        for tree in (program.ast, physical):
            ctx = Context(Value.none())
            ctx.depth = start
            observed.append(_observe(lambda: eval_node(tree, ctx)))
        assert observed[0] == observed[1], (start, observed)
    assert observed[0][:3] == ('E_DEPTH', 1, 1)       # the call itself, past the cap
    ctx = Context(Value.none())
    ctx.depth = MAX_DEPTH - 1
    assert _observe(lambda: eval_node(physical, ctx)) == ('E_DEPTH', 1, 8)   # the first key


# Each condition with its literals as {}: the literal form, which reads them
# where their nodes keep them, answers as the same condition reading each
# literal's value from a variable of the same length (so every position is
# the same), on the plain tree and on the physical one.
CONDITIONS = [
    ('_["n"] > {}', ['1']), ('_["n"] >= {}', ['1.50']), ('_["n"] == {}', ['2']),
    ('_["n"] != {}', ['2.0']), ('_["n"] < {}', ['10']), ('_["n"] > _["m"]', []),
    ('_["t"] $== {}', ['"a"']), ('_["t"] $!= {}', ['"a"']), ('_["t"] $== {}', ['"é"']),
    ('_["t"] $== _["u"]', []), ('_["r"] $== {}', ['"a"']), ('_["b"] $== {}', ['"a"']),
    ('_["x"] $== {}', ['"a"']), ('_["n"] > {} AND _["t"] $== {}', ['1', '"a"']),
    ('_["t"] $== {} OR _["n"] < {}', ['"a"', '0']),
    ('(_["n"] > {}) AND (_["n"] < {} OR _["t"] $!= {})', ['1', '3', '"b"']),
    ('_["n"] > {} AND _["missing"] > {}', ['1', '1']), ('_["t"] > {}', ['1']),
    ('_["n"]', []), ('_["b"] AND _["n"] > {}', ['1']), ('_["b"] > {}', ['1']),
    ('_["r"] > {}', ['1']), ('_["x"] >= {}', ['0']),
]
_VARS = {'1': 'A', '1.50': 'NUMA', '2': 'B', '2.0': 'NMC', '10': 'ND', '0': 'C', '3': 'D',
         '"a"': 'TXA', '"é"': 'TXE', '"b"': 'TXB'}
_ROWS = [{'n': '2', 'm': '1', 't': 'a', 'u': 'a', 'r': {'k': 1}, 'b': True, 'x': 'a'},
         {'n': '0.5', 'm': '3', 't': 'b', 'u': 'c', 'r': 'a', 'b': 'a', 'x': {'k': 'a'}},
         {'n': '-1', 'm': '-1', 't': 'é', 'u': 'é', 'r': 'b', 'b': 'b', 'x': 'b'},
         {'n': 'abc', 'm': '1', 't': 'a', 'u': 'a', 'r': 'a', 'b': False, 'x': 'a'}]


@pytest.mark.parametrize('template, literals', CONDITIONS)
def test_a_condition_holds_on_the_physical_tree_as_on_the_plain_one(template, literals):
    # FILTER, IF, COND and a join's pre-filter ask eval_cond whether a node
    # holds, and a comparison reads a literal operand where its node keeps it:
    # the answers, the errors and their positions are those of the condition
    # read through variables, plain or physical.
    from sel import Value
    as_literal = template.format(*literals)
    as_variable = template.format(*(_VARS[lit] for lit in literals))
    variables = {_VARS[lit]: lit.strip('"') for lit in _VARS}
    for wrap in ('FILTER(L, {})', 'MAP(L, IF({}, 1, 2))', 'MAP(L, COND({0}, 1, {0}, 2, 3))',
                 'MAP(L, {})', 'L .> LINK(L, _1["n"] $== _2["n"]) .> FILTER({})'):
        for rows in (_ROWS, _ROWS[1:], _ROWS[2:3], _ROWS[:1]):
            context = dict(variables, L=rows)
            reference = compile(wrap.format(as_variable))
            expected = _observe(lambda: eval_node(reference.ast, Context(Value.from_native(context))))
            assert _observe(lambda: reference.run(dict(context))) == expected
            program = compile(wrap.format(as_literal))
            assert _observe(lambda: eval_node(program.ast, Context(Value.from_native(context)))) == expected
            for _ in range(2):
                assert _observe(lambda: program.run(dict(context))) == expected, (wrap, rows)


def test_a_condition_meets_the_depth_cap_where_eval_node_does():
    from sel import Value
    from sel.errors import MAX_DEPTH
    from sel.eval import eval_cond
    for src in ('A > 1', 'A $== "a"', 'A > 1 AND B $== "b"', 'A > 9 OR B $!= "b"'):
        program = compile(src)
        physical = program.physical_ast()
        root = Value.from_native({'A': '2', 'B': 'b'})
        for start in range(MAX_DEPTH - 4, MAX_DEPTH + 1):
            seen = []
            for run in (lambda c: eval_node(physical, c).as_bool(physical.pos),
                        lambda c: eval_cond(physical, c)):
                ctx = Context(root)
                ctx.depth = start
                try:
                    seen.append(run(ctx))
                except SelError as e:
                    seen.append((e.code, e.line, e.col))
                assert ctx.depth == start
            assert seen[0] == seen[1], (src, start, seen)
