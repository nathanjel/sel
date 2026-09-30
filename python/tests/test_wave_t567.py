"""T05 (relational edges), T06 (regex portability) and T07 (size budgets) at the
host level: each test states a rule of SPEC 6.4 / 7.3 / 7.4 / 7.8 and is the
Python twin of a conformance case (27-relational-edges, 28-regex-portability,
29-text-binary-budgets), plus the host-only parts the .selt cannot say -- warnings,
the bounded cache, an engine failing, raw exceptions."""
import warnings

import pytest
import sel
from sel import SelError
from sel._limits import MAX_COLLECTION, MAX_TEXT_LEN
from sel.builtins import regex


def run(src):
    return sel.evaluate(src)


def code(src):
    with pytest.raises(SelError) as info:
        run(src)
    return info.value


# --- T07: size budgets ---------------------------------------------------------

@pytest.mark.parametrize('src', [
    f'REPEAT("a", {MAX_TEXT_LEN + 1})',
    f'REPEAT("ab", {MAX_TEXT_LEN // 2 + 1})',
    'REPEAT("ab", 99999999999999999999)',
    f'PADL("a", {MAX_TEXT_LEN + 1}, "x")',
    'PADR("abc", 99999999999999999999999, "x")',
    f'REPEAT("a", {MAX_TEXT_LEN}) & "b"',
    f'TO_HEX(TO_UTF8(REPEAT("a", {MAX_TEXT_LEN // 2 + 1})))',
    f'ENCODE_BASE64(TO_UTF8(REPEAT("a", {MAX_TEXT_LEN * 3 // 4 + 3})))',
    'JOIN(SPLIT(REPEAT("a,", 100) & "a", ","), REPEAT("b", 10000000))',
    'REPLACE("a", REPEAT("b", 200000), REPEAT("a", 200))',
    'COUNT(SPLIT(REPEAT("a,", 1000000) & "a", ","))',
    f'COUNT(BTL(TO_UTF8(REPEAT("a", {MAX_COLLECTION + 1}))))',
])
def test_a_result_past_its_cap_is_e_range_at_the_call(src):
    e = code(src)
    assert e.code == 'E_RANGE'


def test_the_position_is_the_node_that_builds_the_value():
    assert (code('REPEAT("a", 16777217)').line, code('REPEAT("a", 16777217)').col) == (1, 1)
    e = code(f'REPEAT("a", {MAX_TEXT_LEN}) & "b"')
    assert (e.line, e.col) == (1, 23)          # the & operator


def test_an_empty_result_is_never_too_large():
    assert run('REPEAT("", 99999999999999999999)').scalar == ''
    assert run('LEN(REPEAT("", 1 & REPEAT("0", 400)))').scalar == '0'
    assert run('LEN(REPEAT(REPEAT("a", 1000), 0))').scalar == '0'


def test_a_count_that_only_clamps_is_not_an_error():
    assert run('LEFT("abc", 99999999999999999999)').scalar == 'abc'


def test_at_the_cap_still_builds():
    assert run(f'LEN(REPEAT("a", {MAX_TEXT_LEN}))').scalar == str(MAX_TEXT_LEN)
    assert run(f'LEN(PADL("a", {MAX_TEXT_LEN}, "xy"))').scalar == str(MAX_TEXT_LEN)


def test_a_doubling_is_refused_before_it_is_built():
    src = 'A = (1, 2);' + ' A = (A, A);' * 20 + ' COUNT(A)'
    e = code(src)
    assert e.code == 'E_RANGE'


def test_list_and_record_are_capped():
    from sel._budget import check_collection
    with pytest.raises(SelError):
        check_collection(MAX_COLLECTION + 1, None)
    check_collection(MAX_COLLECTION, None)


def test_link_result_is_capped():
    e = code('A = SPLIT(REPEAT("a,", 1000) & "a", ","); B = SPLIT(REPEAT("b,", 999) & "b", ",");'
             ' COUNT(LINK(A, B, TRUE))')
    assert e.code == 'E_RANGE'


def test_ltb_of_an_empty_list_is_an_empty_bin():
    assert run('BLEN(LTB(LIST()))').scalar == '0'
    assert run('TO_HEX(LTB(LIST(65, 1.0)))').scalar == '4101'
    assert run('TO_HEX(LTB(LIST("1.0")))').scalar == '01'
    assert code('LTB(LIST(1.5))').code == 'E_NOT_INT'
    assert code('LTB(LIST(256.0))').code == 'E_RANGE'


# --- T05: relational edges -----------------------------------------------------

def test_an_aggregate_visits_a_snapshot():
    # Appending during the walk does not extend it; overwriting a later element
    # does not change what is visited.
    assert run('A = (1, 2); COUNT(MAP(A, (A[COUNT(A) + 1] = 0; _)))').scalar == '2'
    assert run('A = (1, 2, 3); SUM(A, (A[3] = 100; _))').scalar == '6'


def test_a_scalar_is_a_one_element_list_for_bucket():
    assert run('COUNT(BUCKET("abc", _, _))').scalar == '1'
    assert run('BUCKET("abc", _)["abc"]["1"]').scalar == 'abc'


def test_bucket_two_argument_key_is_an_index_key():
    assert run('x = "a"; x["k"] = 1; y = "a"; y["k"] = 2; COUNT(BUCKET(LIST(x, y), _)["a"])').scalar == '2'


def test_direction_and_count_are_evaluated_on_an_empty_list():
    assert code('SORT_BY(LIST(), _ + 0, "X")').code == 'E_BAD_ARG'
    assert code('SORT_BY(NULL, _, "UP")').code == 'E_BAD_ARG'
    assert code('TOP_BY(LIST(), _, "UP", 1)').code == 'E_BAD_ARG'


def test_total_order_of_every_kind():
    out = run('LIST("1a", 10, "9", TRUE, NULL, FALSE, "007", "b", "") .> SORT()')
    got = [v.scalar for v in out.values()]
    # NULL, then BOOL (FALSE before TRUE), then numeric text by value, then other
    # text bytewise ("" first, then "1a" < "b").
    assert got == [None, False, True, '007', '9', '10', '', '1a', 'b']


def test_sort_is_transitive_on_mixed_text():
    from sel.value import Value
    from sel.builtins.aggregate import compare_values
    vals = [Value.text(x) for x in ('10', '9', '1a', '007', '', ' 2', '1e3', '-0', '0')]
    for a in vals:
        for b in vals:
            for c in vals:
                if compare_values(a, b) <= 0 and compare_values(b, c) <= 0:
                    assert compare_values(a, c) <= 0


def test_select_cols_keeps_a_repeated_column_once():
    assert run('COUNT(SELECT_COLS(LIST(RECORD("a", 1, "b", 2)), "a", "a", "b")[1])').scalar == '2'
    assert run('COUNT(SELECT_COLS(LIST(RECORD("a", 1, "b", 2), RECORD("b", 3, "a", 4)), "a", "b", "a")[2])').scalar == '2'


def test_same_named_join_binders_the_right_shadows_the_left():
    ctx = {'P': [{'k': 1}, {'k': 2}], 'Q': [{'k': 3}, {'k': 4}]}
    for pred in ('x["k"] == x["k"]', 'x["k"] == x["k"] AND TRUE'):
        assert sel.compile(f'COUNT(LINK(P, Q, x, x, {pred}))').run(ctx).scalar == '4'


# --- T06: regex ----------------------------------------------------------------

@pytest.mark.parametrize('pattern', [
    '^*', '$+', '^{2}', 'a$?', '(*FAIL)', '(?:^)*a', '(a*)*', '(?:a?)+', '(|a)+', '(a*?)+',
    '(?:(a)|b)*', '(?:(a)|(b))+', '(?:(a)?b)+', '[+-\\d]', '[\\d-z]', '[a-\\s]', '[!-\\w]',
    '[a[:digit:]', '[a[.x.]]', '[a[=x=]]', '[a--b]', 'a**', '(', 'a)', '*a',
    '(' * 201 + 'a' + ')' * 201, '(?:' * 50000 + 'a' + ')' * 50000,
    '^' + '(a)' * 1001 + '$', '^' + 'a' * 65535 + '$',
])
def test_rejected_pattern(pattern):
    with pytest.raises(SelError) as info:
        regex.validate(pattern)
    assert info.value.code == 'E_REGEX_SYNTAX'


@pytest.mark.parametrize('pattern', [
    '(a|b)+', '((a)b)+', '(\\d+,)+', '(\\d*)?', '^[\\d-]$', '^[-\\d]$', '^[\\w.-]$', '^[.[]$',
    '^[=[]$', '^[[]$', '^[a&&b]$', '^[a||b]$', '^[a~~b]$', '^' + 'a' * 65533 + '$',
    '(' * 200 + 'a' + ')' * 200, '^' + '(a)' * 1000 + '$', '(?:a{60000}){60000}', '^(a){20000}$',
])
def test_accepted_pattern(pattern):
    regex.validate(pattern)


def test_deep_groups_do_not_recurse():
    # 50,000 levels is refused by the parser's own depth rule, without RecursionError.
    with pytest.raises(SelError):
        regex.validate('(?:' * 50000 + 'a' + ')' * 50000)


def test_only_lowercase_i_is_a_flag():
    for flag in ('I', 'İ', 'ı', 'K', 'x'):
        assert code(f'RMATCH("a", "A", "{flag}")').code == 'E_BAD_ARG'
    assert run('RMATCH("^a+$", "AaA", "i")').scalar is True


def test_a_literal_pattern_is_checked_when_the_program_is_compiled():
    for src in ('IF(FALSE, RMATCH("(?=a)", "a"), 1)', "IF(FALSE, RREPLACE('(?=a)', '-', 'a'), 1)",
                "IF(FALSE, RGROUPS('(?=a)', 'a'), 1)", "IF(FALSE, RFIND('(?=a)', 'a'), 1)"):
        with pytest.raises(SelError) as info:
            sel.compile(src)
        assert info.value.code == 'E_REGEX_SYNTAX', src


def test_a_computed_pattern_is_checked_only_when_it_runs():
    assert run('IF(FALSE, RMATCH("(?=" & "a)", "a"), 1)').scalar == '1'
    assert code('IF(TRUE, RMATCH("(?=" & "a)", "a"), 1)').code == 'E_REGEX_SYNTAX'


def test_no_future_warning_leaks_from_set_syntax():
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        regex._cache.clear()
        for pat in ('^[[]$', '^[a&&b]$', '^[a||b]$', '^[a~~b]$', '^[.[]$'):
            assert sel.evaluate(f"RMATCH('{pat}', 'x')").scalar is False
        assert run("RMATCH('^[a&&b]$', '&')").scalar is True
        assert run("RMATCH('^[a~~b]$', '~')").scalar is True


def test_the_pattern_cache_is_bounded():
    regex._cache.clear()
    for i in range(regex._CACHE_MAX + 100):
        sel.evaluate(f'RMATCH("^a{i}$", "x")')
    assert len(regex._cache) == regex._CACHE_MAX


def test_the_ignore_case_flag_is_part_of_the_cache_key():
    regex._cache.clear()
    assert run('RMATCH("^a$", "A")').scalar is False
    assert run('RMATCH("^a$", "A", "i")').scalar is True


def test_rreplace_result_is_capped():
    e = code('RREPLACE("a", REPEAT("b", 40), REPEAT("a", 500000))')
    assert e.code == 'E_RANGE'


def test_rreplace_walks_left_to_right():
    assert run("RREPLACE('a*', '-', 'baac')").scalar == '-b--c-'
    assert run("RREPLACE('b*?', '-', 'abb')").scalar == '-a-b-b-'
