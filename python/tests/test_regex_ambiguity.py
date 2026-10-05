"""The exponential-ambiguity rule (SPEC 7.8, python/sel/builtins/_regex_ambiguity.py)
held to the reference validator (tools/regex-ambiguity-ref.py): the same lists, the
same verdicts, and the failure it exists for: a pattern `re` would take
exponential time on is refused, at compile time when it is a literal."""
import importlib.util
import os
import sys
import time

import pytest
import sel
from sel import SelError
from sel.builtins import regex

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    'regex_ref', os.path.join(HERE, '..', '..', 'tools', 'regex-ambiguity-ref.py'))
ref = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ref)


def verdict(pattern, ic=False):
    try:
        regex.validate(pattern, None, ic)
        return True
    except SelError as e:
        assert e.code == 'E_REGEX_SYNTAX', (pattern, e.code)
        return False


@pytest.fixture(autouse=True)
def default_recursion_limit():
    before = sys.getrecursionlimit()
    sys.setrecursionlimit(1000)
    regex._accepted.clear()
    yield
    sys.setrecursionlimit(before)


@pytest.mark.parametrize('pattern', ref.MUST_REJECT)
def test_exponential_patterns_are_refused(pattern):
    assert verdict(pattern) is False


@pytest.mark.parametrize('pattern', ref.MUST_ACCEPT)
def test_legitimate_patterns_are_accepted(pattern):
    assert verdict(pattern) is True


@pytest.mark.parametrize('pattern', ref.FLAG_PAIRS)
def test_the_i_flag_is_part_of_the_analysis(pattern):
    assert verdict(pattern, ic=False) is True
    assert verdict(pattern, ic=True) is False


@pytest.mark.parametrize('pattern,ok', ref.BUDGET)
def test_budget_boundaries(pattern, ok):
    assert verdict(pattern) is ok


@pytest.mark.parametrize('pattern', ref.STRUCTURAL_REJECT)
def test_structural_errors_are_still_refused(pattern):
    assert verdict(pattern) is False


@pytest.mark.parametrize('pattern', ref.STRUCTURAL_ACCEPT)
def test_structural_accepts(pattern):
    assert verdict(pattern) is True


def test_every_reference_list_agrees_with_the_reference_verdict():
    for pattern in ref.MUST_REJECT + ref.MUST_ACCEPT:
        assert verdict(pattern) is ref_verdict(pattern), pattern


def test_the_accepted_verdict_is_remembered_per_pattern_and_flag():
    assert verdict(r'(\d+,)+') is True
    assert (False, r'(\d+,)+') in regex._accepted
    assert (True, r'(\d+,)+') not in regex._accepted


def test_the_accepted_memory_is_bounded():
    for i in range(regex._ACCEPTED_MAX + 50):
        assert verdict('a{%d}b' % (i + 1)) is True
    assert len(regex._accepted) <= regex._ACCEPTED_MAX


def test_a_literal_pattern_that_hangs_re_is_refused_when_the_program_compiles():
    with pytest.raises(SelError) as info:
        sel.compile('IF(FALSE, RMATCH("^(a+)+$", "x"), 1)')
    assert info.value.code == 'E_REGEX_SYNTAX'


def test_a_computed_pattern_is_refused_when_it_runs_not_after_running_for_minutes():
    t = time.process_time()
    with pytest.raises(SelError) as info:
        sel.evaluate('P = "^(a+)" & "+$"; RMATCH(P, REPEAT("a", 40) & "!")')
    assert info.value.code == 'E_REGEX_SYNTAX'
    assert time.process_time() - t < 2


def test_the_pre_fix_failure_is_gone_for_the_review_repros():
    for pat in ('^(a+)+$', '(a|aa)+$', '(a|b|ab)*c'):
        t = time.process_time()
        with pytest.raises(SelError):
            sel.evaluate('RMATCH(\'%s\', REPEAT("a", 40) & "!")' % pat)
        assert time.process_time() - t < 2


def ref_verdict(pattern, ic=False):
    # the reference recurses per group; give it the room it asks for
    before = sys.getrecursionlimit()
    sys.setrecursionlimit(20000)
    try:
        ref.validate(pattern, ic)
        return True
    except ref.Reject:
        return False
    finally:
        sys.setrecursionlimit(before)


def test_depth_200_of_groups_analyses_under_the_default_recursion_limit():
    for nested in ('(?:' * 200 + 'a' + ')' * 200,
                   '(?:' * 200 + 'a' + ')?' * 200,
                   '(?:' * 100 + '(a{2}|b)' + ')+' * 100,
                   '(' * 200 + 'a' + ')' * 200):
        assert verdict(nested) is ref_verdict(nested), nested[:20]
    assert verdict('(?:' * 200 + 'a' + ')' * 200) is True
    assert sys.getrecursionlimit() == 1000
    assert verdict('(?:' * 201 + 'a' + ')' * 201) is False       # depth cap, unchanged


def test_hostile_budget_overruns_are_refused_quickly():
    t = time.process_time()
    for pattern in ('a?' * 8 + 'b', '(a|a)' * 9 + 'x', '(?:a{1,20}){1,20}b'):
        assert verdict(pattern) is False
        assert ref_verdict(pattern) is False
    assert verdict('(a|a)' * 2000) is False                      # also the group-count cap
    assert time.process_time() - t < 5


def test_big_but_legitimate_patterns_are_fast():
    t = time.process_time()
    assert verdict('a{65535}') is True
    assert verdict('^[a-z]{1,65535}$') is True
    assert verdict('(?:a|b|c|d|e)*') is True
    assert time.process_time() - t < 5


def test_a_large_alternation_in_a_loop_hits_the_edge_cap_like_the_reference():
    # A known false rejection: 2000 alternatives under a loop join to 2000 x 2000 edges.
    pattern = '(?:' + '|'.join('w%d' % i for i in range(2000)) + ')+'
    assert verdict(pattern) is ref_verdict(pattern) is False


def test_case_folding_reaches_the_analysis_including_the_two_non_ascii_letters():
    # k and K fold together with U+212A and s/S with U+017F: `[a-z]` and `[K]` overlap under i
    assert verdict('(?:[a-z]|K)+$', ic=False) is True
    assert verdict('(?:[a-z]|K)+$', ic=True) is False
    # fold first, negate after: [^k] under i excludes k, K and the Kelvin sign
    assert verdict('(?:[^k]|K)+$', ic=True) is True


def test_a_negated_escape_negates_before_folding():
    # \D is negated then folded; the two spellings are different sets under i
    assert verdict(r'(?:\D|a)+$', ic=True) is False
    assert verdict(r'(?:[^\d]|a)+$', ic=False) is False
