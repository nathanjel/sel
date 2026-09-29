"""Findings of the first full gate run (differential fuzz): a value with children
and no scalar is sorted by what scalar context makes of it (SPEC 3.2 / 7.3), ties
keep input order in both directions, and a bad literal regex flag never stops the
compile-time check of the pattern (SPEC 7.8)."""
import pytest
import sel
from sel import SelError


def text(src):
    return sel.evaluate(src).scalar


ROWS = 'LIST(RECORD("k", 3, "v", "c"), RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"))'
TIES = ('LIST(RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"), '
        'RECORD("k", 1.0, "v", "c"), RECORD("k", "1", "v", "d"))')


def joined(expr):
    return text('JOIN(MAP(%s, _["v"]), ",")' % expr)


def test_records_sort_by_their_first_field():
    assert joined(ROWS + ' .> SORT()') == 'a,b,c'
    assert joined(ROWS + ' .> SORT_DESC()') == 'c,b,a'


def test_tied_records_keep_input_order_in_both_directions():
    assert joined(TIES + ' .> SORT()') == 'a,c,d,b'
    assert joined(TIES + ' .> SORT_DESC()') == 'b,a,c,d'
    assert joined(TIES + ' .> TOP_DESC(4)') == 'b,a,c,d'


def test_a_record_ranks_by_the_kind_of_its_first_field():
    assert joined('LIST(RECORD("k", "b", "v", "b"), RECORD("k", 5, "v", "n"), RECORD("k", "a", "v", "a")) .> SORT()') == 'n,a,b'
    assert joined('LIST(RECORD("k", 5, "v", "n"), RECORD("k", TRUE, "v", "t"), RECORD("k", FALSE, "v", "f")) .> SORT()') == 'f,t,n'


@pytest.mark.parametrize('src,col', [
    ('RMATCH(\'\\p{L}\', " 2", "x")', 8),
    ('RGROUPS(\'(?=a)\', "", "x")', 9),
    ('IF(FALSE, RMATCH(\'(?=a)\', "x", "x"), 1)', 18),
])
def test_a_bad_pattern_beside_a_bad_flag_is_the_patterns_compile_error(src, col):
    with pytest.raises(SelError) as info:
        sel.compile(src)
    assert (info.value.code, info.value.line, info.value.col) == ('E_REGEX_SYNTAX', 1, col)


def test_a_bad_flag_beside_a_good_pattern_waits_for_run_time():
    program = sel.compile('RMATCH("a", "a", "x")')
    with pytest.raises(SelError) as info:
        program.run({})
    assert info.value.code == 'E_BAD_ARG'
