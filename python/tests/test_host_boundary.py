"""The host boundary (spec/SPEC.md §8): what fromNative, toNative and the
constructors promise, and that a compiled program keeps nothing between runs.

The conformance suite cannot reach any of this — its setup is SEL source, not
native data — so each promise is pinned here and in every other host's lane
(review 2026-09-25, HOST-01..10).
"""

import pytest

from sel import SelError, Value, compile as sel_compile


def code_of(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except SelError as e:
        return e.code
    return None


def deep(levels):
    v = Value.text('x')
    for _ in range(levels):
        v = Value.list([v])
    return Value.list([v])


# --- HOST-01: a scalar and a child named "_" cannot share one native map

def test_to_native_refuses_a_scalar_with_a_child_named_underscore():
    v = sel_compile('A = "s"; A["_"] = "c"; A').run()
    assert code_of(v.to_native) == 'E_BAD_ARG'


# --- HOST-02 / HOST-03: the boundary copies

def test_bin_copies_a_bytearray():
    b = bytearray(b'\x01')
    v = Value.bin(b)
    b[0] = 2
    assert v.dump() == 'b01'


def test_from_native_copies_a_bytearray():
    b = bytearray(b'\x01')
    v = Value.from_native({'k': b})
    b[0] = 2
    assert v.dump() == '-{"k"=b01}'


# --- HOST-04: bytes are whole numbers 0..255

@pytest.mark.parametrize('bad', [256, -1, 1.5, '1'])
def test_bin_rejects_what_is_not_a_byte(bad):
    assert code_of(Value.bin, [bad]) == 'E_RANGE'


# --- HOST-05: every text entering is checked, keys included

def test_text_rejects_a_lone_surrogate():
    assert code_of(Value.text, '\ud800') == 'E_UTF8'


def test_from_native_rejects_a_lone_surrogate_key():
    assert code_of(Value.from_native, {'\ud800': 'x'}) == 'E_UTF8'


def test_set_rejects_a_lone_surrogate_key():
    assert code_of(Value.none().set, '\udc00', Value.text('x')) == 'E_UTF8'


# --- HOST-06: the digit caps hold for native integers

def test_int_at_the_digit_cap_is_a_number():
    v = Value.int(10 ** 999999)
    assert sel_compile('LEN(A) == 1000000').run({'A': v}).dump() == 'TRUE'


@pytest.mark.parametrize('build', [Value.int, Value.from_native])
def test_int_past_the_digit_cap_is_e_range(build):
    assert code_of(build, 10 ** 1000000) == 'E_RANGE'


def test_negative_int_past_the_digit_cap_is_e_range():
    assert code_of(Value.int, -(10 ** 1000000)) == 'E_RANGE'


# --- HOST-07: an over-deep host value cannot be hashed any more than dumped

@pytest.mark.parametrize('src', ['COUNT(DEDUPE(A))', 'COUNT(DISTINCT(A))',
                                 'COUNT(BUCKET(A, _, COUNT(_)))'])
def test_hash_walks_respect_the_depth_cap(src):
    assert code_of(sel_compile(src).run, {'A': deep(250)}) == 'E_DEPTH'
    assert sel_compile(src).run({'A': deep(198)}).dump() == 't"1"'


# --- HOST-08 / HOST-09: to_native and from_native are inverses

@pytest.mark.parametrize('src', ['FILTER(LIST(1,2,3), _ > 1)', 'RECORD("0","a","1","b")',
                                 'FALSE', 'RECORD("a", FALSE)', 'LIST(TRUE, NULL)'])
def test_native_round_trip(src):
    v = sel_compile(src).run()
    assert Value.from_native(v.to_native()).dump() == v.dump()


# --- HOST-10: a compiled program keeps nothing from one run to the next

def test_compiled_program_reads_the_key_of_each_run():
    p = sel_compile('A[K]')
    a = Value.from_native({'x': '1', 'y': '2'})
    assert p.run({'A': a, 'K': 'x'}).dump() + p.run({'A': a, 'K': 'y'}).dump() == 't"1"t"2"'


def test_compiled_program_reads_the_computed_key_of_each_run():
    p = sel_compile('MAP(L, A[_])')
    a = Value.from_native({'x': '1', 'y': '2'})
    assert p.run({'A': a, 'L': ['x']}).dump() == '-{"1"=t"1"}'
    assert p.run({'A': a, 'L': ['y']}).dump() == '-{"1"=t"2"}'
