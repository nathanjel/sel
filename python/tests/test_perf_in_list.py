"""`x IN <literal list>` is built once and, for text/number lists, answered
by a set lookup. It must answer exactly as the per-row walk does (EQL: numbers are
not normalised, kinds must match, NULL IN list looks for a NULL)."""
import pytest
import sel
from sel.eval import Context, eval_node
from sel.value import Value

NEEDLES = ['a', 'b', 'zz', '', '7', '7.0', '7.50', '007', '-0', 'é', True, False, None,
           {'k': 'a'}, ['a', 'b'], 7, '8']


def plain(src, ctx):
    """The tree exactly as parsed: no optimiser, no constant."""
    p = sel.compile(src)
    return eval_node(p.ast, Context(Value.from_native(dict(ctx)))).dump()


def literal_lists():
    yield '("a", "b", "7")'
    yield 'LIST("a", "b", "7", 7.50, 8)'
    yield '("a", TRUE, 7)'
    yield '("a", NULL)'
    yield '(TRUE, FALSE)'
    yield '("é", "zz", "")'
    yield '("007", "-0", "7.0")'
    yield '("7.50", 7.50)'


@pytest.mark.parametrize('lst', list(literal_lists()))
@pytest.mark.parametrize('needle', NEEDLES, ids=repr)
def test_constant_list_answers_like_the_walk(lst, needle):
    src = 'N IN ' + lst
    p = sel.compile(src)
    got = p.run({'N': needle}).dump()
    assert got == plain(src, {'N': needle})
    # and the physical tree really holds a constant for it
    assert p.physical_ast().const_value is not None


def test_per_row_use_through_filter_matches_the_walk():
    src = 'ROWS .> FILTER(_ IN ("a", "b", "7", 7.50)) .> COUNT()'
    texts = ['a', 'b', 'c', '7', '7.5', '7.50', 'zz']
    got = sel.compile(src).run({'ROWS': texts}).dump()
    want = plain(src, {'ROWS': texts})
    assert got == want == 't"4"'


def test_a_variable_inside_the_list_is_not_a_constant():
    p = sel.compile('N IN ("a", V)')
    assert p.physical_ast().const_value is None
    assert p.run({'N': 'q', 'V': 'q'}).dump() == plain('N IN ("a", V)', {'N': 'q', 'V': 'q'})


def test_a_list_past_the_depth_cap_still_raises_as_written():
    src = 'N IN ' + '(' * 100 + '"a", "b"' + ')' * 100
    with pytest.raises(sel.SelError) as info:
        sel.compile(src)
    assert info.value.code == 'E_DEPTH'
