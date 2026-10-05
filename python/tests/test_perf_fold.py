"""A folded numeric literal carries its decoded Dec, so evaluation does not
re-parse it, and folding over an already decoded operand gives the same text."""
import sel
from sel import decimal as D


def folded(src):
    return sel.compile(src).physical_ast()


def test_a_folded_literal_carries_its_dec():
    for src, text in (('1 + 2', '3'), ('1.5 * 2', '3.0'), ('7 / 2', '3.5'),
                      ('10 % 4', '2'), ('-(2)', '-2'), ('-3.50', '-3.50')):
        n = folded(src)
        assert n.t == 'num', src
        assert n.v == text, (src, n.v)
        assert n.dec is not None and D.format(n.dec) == n.v, src


def test_chained_folds_reuse_the_decoded_operands():
    n = folded('(1 + 2) * (3 - 1) / 4')
    assert (n.t, n.v) == ('num', '1.5')
    assert D.format(n.dec) == '1.5'


def test_folding_behaviour_is_unchanged():
    p = sel.compile('A > 1.5 * 2 AND B == 4 - 1 AND 10 % 4 == 2')
    assert p.run({'A': '3.5', 'B': '3'}).dump() == sel.compile(
        'A > 3.0 AND B == 3 AND TRUE').run({'A': '3.5', 'B': '3'}).dump()
    # an error in a folded operand still surfaces where the evaluator puts it
    try:
        sel.compile('1 / 0').run({})
    except sel.SelError as e:
        assert e.code == 'E_DIV_ZERO' and (e.line, e.col) == (1, 3)
    else:
        raise AssertionError('1 / 0 must fail')
