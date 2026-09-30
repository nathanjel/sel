"""PY-P30: the i flag's per-call work (ASCII pattern test, subject folding) and the no-flag
path. Results and errors must be what the character loop and unconditional translate gave."""
import pytest

import sel
from sel import SelError
from sel.builtins import regex as R


def run(src, **ctx):
    return sel.evaluate(src, ctx)


def test_fold_subject_is_the_translate_for_non_ascii_and_the_identity_for_ascii():
    assert R._fold_subject('abc') == 'abc'
    assert R._fold_subject('') == ''
    assert R._fold_subject('Kſ') == 'ks'                # Kelvin sign, long s
    assert R._fold_subject('xKy') == 'xky'
    assert R._fold_subject('zażółć') == 'zażółć'


def test_the_i_flag_folds_kelvin_and_long_s_but_nothing_else():
    assert run('RMATCH("^k$", "\\u{212A}", "i")').as_bool(None) is True
    assert run('RMATCH("^s$", "\\u{17F}", "i")').as_bool(None) is True
    assert run('RMATCH("^K$", "k", "i")').as_bool(None) is True
    assert run('RMATCH("^k$", "K")').as_bool(None) is False
    assert run('RFIND("S", "xxs", "i")').scalar == '3'


def test_groups_come_from_the_original_subject_not_the_folded_one():
    out = run('RGROUPS("a(k)b", "A\\u{212A}B", "i")')
    texts = [v.scalar for _, v in sel.value.elements(out)]
    assert texts == ['AKB', 'K']


def test_a_non_ascii_pattern_with_the_i_flag_is_still_refused_at_the_flag():
    with pytest.raises(SelError) as info:
        run('RMATCH("zażółć", "x", "i")')
    assert info.value.code == 'E_BAD_ARG'
    assert run('RMATCH("zażółć", "zażółć")').as_bool(None) is True       # no flag: fine


@pytest.mark.parametrize('flags', ['I', 'm', 's', 'x', 'ii', 'g'])
def test_other_flags_are_still_bad_args(flags):
    if flags == 'ii':
        assert run('RMATCH("a", "A", "ii")').as_bool(None) is True       # repeated i is still i
        return
    with pytest.raises(SelError) as info:
        run('RMATCH("a", "a", "%s")' % flags)
    assert info.value.code == 'E_BAD_ARG'


def test_empty_flags_and_no_flags_agree():
    assert run('RMATCH("^a", "abc", "")').as_bool(None) is True
    assert run('RMATCH("^a", "abc")').as_bool(None) is True
