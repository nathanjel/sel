"""Unit tests for the layers underneath the conformance suite.

conformance/ is what proves this implementation correct, and it is normative;
nothing here duplicates it. These tests cover the internals a conformance
failure would only point at indirectly — the UTF-8 codec, the decimal core, the
value model's ordering and dump — plus the public host interface, which the
conformance runner exercises only in one shape.

A handful exist specifically to pin *Python's* traps: the places where a
convenient built-in means something wider than SEL does. Those say so.
"""

import pytest

from sel import BOOL, SelError, Value, compile as sel_compile, evaluate
from sel import decimal as D
from sel.utf8 import bytes_compare, decode_utf8, encode_utf8


def raises(code, fn, *a, **kw):
    with pytest.raises(SelError) as ei:
        fn(*a, **kw)
    assert ei.value.code == code, f'wanted {code}, got {ei.value.code}: {ei.value.message}'
    return ei.value


# --- utf8 -------------------------------------------------------------------

def test_utf8_round_trip():
    for s in ['', 'ascii', 'héllo', 'Zażółć gęślą jaźń', '𝄞 clef']:
        assert decode_utf8(encode_utf8(s)) == s


@pytest.mark.parametrize('bad', [
    b'\xff',                  # invalid start byte
    b'\xc0\x80',              # overlong 2-byte
    b'\xe0\x80\x80',          # overlong 3-byte
    b'\xf0\x80\x80\x80',      # overlong 4-byte
    b'\xed\xa0\x80',          # surrogate
    b'\xf4\x90\x80\x80',      # above U+10FFFF
    b'\xe2\x8c',              # truncated
    b'\xc2',                  # truncated
])
def test_utf8_strict_decoding(bad):
    """None of these may become U+FFFD."""
    raises('E_UTF8', decode_utf8, bad)


def test_utf8_rejects_lone_surrogate():
    """Python str permits a lone surrogate; UTF-8 has no encoding for one."""
    raises('E_UTF8', encode_utf8, chr(0xD800))
    raises('E_UTF8', encode_utf8, 'a' + chr(0xDFFF) + 'b')
    raises('E_UTF8', Value.text, chr(0xD800))


def test_bytes_compare_is_bytewise():
    assert bytes_compare(b'\x00', b'\x01') == -1
    assert bytes_compare(b'ab', b'ab') == 0
    assert bytes_compare(b'ab', b'a') == 1
    assert bytes_compare(encode_utf8('�'), encode_utf8('\U00010000')) == -1


# --- decimal ----------------------------------------------------------------

def test_decimal_canonical_form():
    """Leading zeros go, trailing fraction zeros stay, zero is never negative."""
    assert D.format(D.parse('007')) == '7'
    assert D.format(D.parse('2.50')) == '2.50'
    assert D.format(D.parse('-0.00')) == '0.00'
    assert D.format(D.parse('-0')) == '0'


@pytest.mark.parametrize('text', [' 2', '2 ', '1.', '.5', '', 'x', '+1', '1e3',
                                  '٣', '1_2', '1,5'])
def test_decimal_rejects_non_numbers(text):
    """No trimming, no exponents, no underscores, no Unicode digits.

    The last three are Python's own trap: int('1_2') is 12, int(' 12 ') is 12
    and int('٣') is 3, so nothing may reach int() unvalidated.
    """
    assert D.parse(text) is None


def test_decimal_scale_rules():
    assert D.format(D.add(D.parse('1.50'), D.parse('2.50'))) == '4.00'
    assert D.format(D.mul(D.parse('2.5'), D.parse('4'))) == '10.0'
    assert D.format(D.div(D.parse('4'), D.parse('2'))) == '2'
    assert D.format(D.div(D.parse('1'), D.parse('3'))) == '0.3333333333'


def test_decimal_rounds_half_away_from_zero():
    """Not Python's round(), which is half to even: round(2.5) is 2 there."""
    assert D.format(D.round(D.parse('2.5'), 0)) == '3'
    assert D.format(D.round(D.parse('-2.5'), 0)) == '-3'
    assert D.format(D.round(D.parse('0.5'), 0)) == '1'
    assert round(2.5) == 2 and round(0.5) == 0        # the trap, demonstrated


def test_decimal_division_by_zero():
    raises('E_DIV_ZERO', D.div, D.parse('1'), D.parse('0'))
    raises('E_DIV_ZERO', D.mod, D.parse('1'), D.parse('0'))


def test_decimal_mod_takes_dividend_sign():
    assert D.format(D.mod(D.parse('-7'), D.parse('3'))) == '-1'
    assert D.format(D.mod(D.parse('7'), D.parse('-3'))) == '1'


def test_decimal_power():
    assert D.format(D.power(D.parse('10'), 3)) == '1000'
    assert D.format(D.power(D.parse('2'), 0)) == '1'
    assert D.format(D.power(D.parse('1.5'), 2)) == '2.25'


# --- value ------------------------------------------------------------------

def test_children_keep_insertion_order():
    """Re-assigning an existing key keeps its position — order is normative, and
    Python dicts give it for free as long as nothing deletes and re-adds.
    """
    v = Value.none()
    v.set('b', Value.text('1'))
    v.set('a', Value.text('2'))
    assert v.keys() == ['b', 'a']
    v.set('b', Value.text('9'))
    assert v.keys() == ['b', 'a']
    assert v.size() == 2


def test_eql_is_structural_not_numeric():
    assert Value.text('5').eql(Value.text('5'))
    assert not Value.text('5.00').eql(Value.text('5'))
    assert not Value.text('5').eql(Value.bool(True))


def test_clone_is_deep():
    a = Value.list([Value.list([Value.text('x')])])
    b = a.clone()
    b.get('1').set('1', Value.text('y'))
    assert a.dump() == '-{"1"=-{"1"=t"x"}}'
    assert b.dump() == '-{"1"=-{"1"=t"y"}}'


def test_scalar_context_takes_first_child():
    assert evaluate('(7, 8)').as_text() == '7'
    raises('E_NO_SCALAR', Value.none().as_text)


def test_dump_escapes():
    assert Value.text('a"b\\c\nd\te').dump() == 't"a\\"b\\\\c\\nd\\te"'
    assert Value.text(chr(1)).dump() == 't"\\u0001"'
    assert Value.bin(b'\x00\xff\x10').dump() == 'b00ff10'


def test_num_validates_at_the_boundary():
    raises('E_NOT_NUM', Value.num, 'x')
    assert Value.num('007').dump() == 't"7"'


def test_from_native_refuses_float():
    """There is no floating point in SEL, and the boundary is where to say so."""
    with pytest.raises(TypeError):
        Value.from_native(0.1)
    with pytest.raises(TypeError):
        Value.from_native({'A': 1.5})


def test_from_native_bool_is_not_int():
    """bool subclasses int in Python; checked first, or True becomes "1"."""
    assert Value.from_native(True).kind == BOOL
    assert Value.from_native(1).kind == 'TEXT'


def test_to_native_round_trip():
    assert Value.from_native({'A': '1', 'B': ['x', 'y']}).to_native() == \
        {'A': '1', 'B': {'1': 'x', '2': 'y'}}


# --- host interface ---------------------------------------------------------

def test_context_is_mutated_in_place():
    ctx = Value.none()
    ctx.set('TOTAL', Value.num('10.00'))
    evaluate('SEEN = TOTAL * 2', ctx)
    assert ctx.get('SEEN').as_text() == '20.00'


def test_dependencies_excludes_assigned_and_bound():
    assert sel_compile('IF(A > B, A, C)').dependencies() == ['A', 'B', 'C']
    assert sel_compile('X = 1; X + Y').dependencies() == ['Y']
    assert sel_compile('ALL(I, IT, IT > 0)').dependencies() == ['I']
    assert sel_compile('ALL(I, _ > 0)').dependencies() == ['I']


def test_run_accepts_a_plain_dict():
    assert evaluate('A & B', {'A': 'x', 'B': 'y'}).as_text() == 'xy'


def test_error_carries_code_and_position():
    """Position and code are the contract; the message is not."""
    e = raises('E_UNDEF_VAR', evaluate, '1 +\n  X')
    assert (e.line, e.col) == (2, 3)
    assert e.offset == 6


def test_compile_time_errors():
    raises('E_UNKNOWN_FUNC', sel_compile, 'NOPE(1)')
    raises('E_ARITY', sel_compile, 'LEN()')
    raises('E_ARITY', sel_compile, 'LEN(1, 2)')
    raises('E_SYNTAX', sel_compile, '1 +')
    raises('E_BAD_ASSIGN', sel_compile, '1 = 2')
    raises('E_RESERVED', sel_compile, 'AND = 1')


def test_short_circuit_never_reaches_the_right_side():
    assert evaluate('FALSE AND (1/0 == 1)').as_bool() is False
    assert evaluate('TRUE OR (1/0 == 1)').as_bool() is True


def test_left_to_right_evaluation_is_observable():
    """Which operand's position an error reports is part of the contract."""
    e = raises('E_NOT_BIN', evaluate, 'TRUE $== FALSE')
    assert e.col == 1                      # the LEFT operand, not the right


# --- parser: the precedence-climbing pilot ----------------------------------

def test_not_is_a_loose_prefix_operator():
    """NOT binds looser than comparison, so it takes the whole comparison."""
    assert evaluate('NOT 1 == 2').as_bool() is True
    assert evaluate('NOT TRUE AND TRUE').as_bool() is False   # (NOT TRUE) AND TRUE


def test_not_is_refused_where_its_binding_power_does_not_reach():
    """Inside a comparison operand NOT is not a prefix operator, so it is read
    as the reserved word it is — the same answer the transcribed parsers give.
    """
    raises('E_RESERVED', sel_compile, '1 == NOT TRUE')
    raises('E_RESERVED', sel_compile, '-NOT TRUE')


def test_comparison_does_not_chain():
    raises('E_SYNTAX', sel_compile, '1 < 2 < 3')
    assert evaluate('(1 < 2) AND (2 < 3)').as_bool() is True


def test_comma_and_semicolon_build_n_ary_nodes():
    assert sel_compile('1, 2, 3').ast.t == 'list'
    assert len(sel_compile('1, 2, 3').ast.items) == 3
    assert sel_compile('1; 2; 3').ast.t == 'seq'
    assert len(sel_compile('1; 2; 3').ast.items) == 3


def test_grouped_flag_distinguishes_one_list_from_two_arguments():
    assert evaluate('JOIN((1, 2), "-")').as_text() == '1-2'
    raises('E_BAD_ASSIGN', sel_compile, '(A) = 1')


def test_prefix_chains_are_depth_counted():
    """Uncounted, these reached the host's own stack limit instead of E_DEPTH."""
    raises('E_DEPTH', sel_compile, '-' * 300 + '1')
    raises('E_DEPTH', sel_compile, 'NOT ' * 300 + 'TRUE')


def test_deep_nesting_raises_e_depth_not_recursion_error():
    """The reason this parser is precedence climbing: a transcribed one needs
    about 3500 Python frames to reach the same trip point, against a default
    recursion limit of 1000.
    """
    e = raises('E_DEPTH', sel_compile, '(' * 100 + '1' + ')' * 100)
    assert (e.line, e.col) == (1, 101)


# --- regex: Python's folding disagreement -----------------------------------

def test_ignore_case_folds_exactly_two_non_ascii_code_points():
    assert evaluate("RMATCH('^k$', \"K\", 'i')").as_bool() is True
    assert evaluate("RMATCH('^s$', \"ſ\", 'i')").as_bool() is True
    # Python's re.IGNORECASE also folds these two, and must not here.
    assert evaluate("RMATCH('^i$', \"İ\", 'i')").as_bool() is False
    assert evaluate("RMATCH('^i$', \"ı\", 'i')").as_bool() is False
    # Full folding would make this true; SEL specifies simple folding.
    assert evaluate("RMATCH('^ss$', \"ß\", 'i')").as_bool() is False


def test_folded_subject_does_not_leak_into_groups():
    """Offsets come from the folded subject, text from the original."""
    v = evaluate("RGROUPS('(k)', \"K\", 'i')")
    assert v.get('2').as_text() == 'K'
    assert evaluate("RREPLACE('k', '[$0]', \"K\", 'i')").as_text() == '[K]'


def test_anchors_do_not_match_before_a_trailing_newline():
    """Python's $ does, like PCRE's; SEL anchors to the ends of the subject."""
    assert evaluate('RMATCH(\'^a$\', "a\\n")').as_bool() is False
    assert evaluate('RMATCH(\'^a$\', "a")').as_bool() is True
