"""Value ownership and the decimal boundaries: what an aggregate or a
constructor collects is a copy, a value past the cap is refused where it is
built, and the numeric core does not depend on process-global settings
(SPEC 3.4; conformance/25-value-ownership.selt)."""
import subprocess
import sys

import pytest
import sel
from sel import SelError, Value, registry


def ev(src, ctx=None):
    return sel.evaluate(src, ctx)


def error(src):
    with pytest.raises(SelError) as info:
        ev(src)
    return info.value


# --- copy semantics (SPEC 3.4) -------------------------------------------------

COPYING = [
    'MAP(X, _)',
    'FILTER(X, TRUE)',
    'SORT(X)',
    'SORT(X, 1)',
    'SORT_DESC(X, 1)',
    'SORT_BY(X, 1)',
    'TOP(X, 1)',
    'TOP(X, 1, 1)',
    'TOP_DESC(X, 1, 1)',
    'TOP_BY(X, 1, 1)',
    'LIST(X[1])',
    'BUCKET(X, 1, _)[1]',
    'BUCKET(X, _["k"], _)[1]',
]


@pytest.mark.parametrize('collect', COPYING)
def test_the_result_holds_a_copy_of_what_it_collected(collect):
    # The result is built first, then the source element is changed through X
    # while the result is still pending in the index expression; the result
    # must not see it. (An assignment `R = ...` would copy on its own and hide
    # the aggregate's aliasing, so the pending-index form is the observation.)
    src = f'X = LIST(RECORD("k",1)); ({collect})[(X[1]["k"] = 9; 1)]'
    out = ev(src)
    while out.get('k') is None:                 # descend BUCKET's / LIST's nesting
        out = out.values()[0]
    assert out.get('k').scalar == '1'


def test_bucket_two_argument_holds_copies():
    assert ev('X = LIST(RECORD("k",1)); BUCKET(X, 1)["1"][(X[1]["k"] = 9; 1)]["k"]').scalar == '1'


def test_record_holds_a_copy():
    assert ev('X = LIST(RECORD("k",1)); RECORD("r", X[1])["r"][(X[1]["k"] = 9; "k")]').scalar == '1'
    assert ev('X = LIST(RECORD("k",1)); RECORD("r", X)["r"][(X[1]["k"] = 9; 1)]["k"]').scalar == '1'


def test_map_over_a_fresh_body_is_not_copied_needlessly():
    # A computed scalar is the body's own; nothing else can reach it.
    out = ev('MAP(LIST(1, 2, 3), _ + 1)')
    assert [v.scalar for v in out.values()] == ['2', '3', '4']


def test_take_aliases_its_elements_but_returns_a_new_container():
    assert ev('X = LIST(RECORD("k",1)); TAKE(X, 1)[(X[1]["k"] = 9; 1)]["k"]').scalar == '9'
    assert ev('X = LIST(1, 2); TAKE(X, 2)[(X[1] = 7; 1)]').scalar == '1'


# --- write-free programs (Context.write_free) ----------------------------------
#
# SPEC 3.4 lets a host leave a collector's or a constructor's copy out where
# nothing can tell: no assignment and no application function in the program.
# Such a program holds the context's own elements; anything that may write
# brings every copy back; and the depth check the copy made is still made.

def _x_context():
    return Value.from_native({'X': [{'k': 1}, {'k': 2}]})


HELD = [
    'MAP(X, _)[1]',
    'FILTER(X, TRUE)[1]',
    'FILTER(X, _["k"] > 0)[1]',
    'SORT(X)[1]',
    'SORT_BY(X, _["k"])[1]',
    'TOP_BY(X, _["k"], 1)[1]',
    'TOP(X, 2)[1]',
    'BUCKET(X, 1)["1"][1]',
    'BUCKET(X, 1, _)[1][1]',
    'RECORD("a", X[1])["a"]',
    'LIST(X[1])[1]',
    '(X, 0)[1]',
    'X .> FILTER(_["k"] > 0) .> SORT_BY(_["k"]) .> TAKE(1) .> MAP(_)[1]',
]


@pytest.mark.parametrize('src', HELD)
def test_a_write_free_program_holds_the_contexts_own_element(src):
    ctx = _x_context()
    program = sel.compile(src)
    assert program.run(ctx) is ctx.get('X').get('1')
    assert program._physical_plan()[1] is True


@pytest.mark.parametrize('src', HELD)
def test_an_assignment_anywhere_brings_the_copy_back(src):
    ctx = _x_context()
    program = sel.compile('Y = 0; ' + src)
    out = program.run(ctx)
    assert out is not ctx.get('X').get('1')
    assert out.eql(ctx.get('X').get('1'))
    assert program._physical_plan()[1] is False


def _poke_writes_through_filters_result(register):
    # A function of the application's may write (SPEC 3.4): this one rewrites
    # X[1] after FILTER collected it, and the write must not reach the result.
    ctx = _x_context()
    x1 = ctx.get('X').get('1')

    def poke(*_):
        x1.set('k', Value.int(9))
        return Value.text('1')

    register(poke)
    try:
        return sel.compile('FILTER(X, TRUE)[T_POKE()]["k"]').run(ctx).scalar
    finally:
        registry._table.pop('T_POKE', None)
        registry._host.discard('T_POKE')


def test_a_registered_function_brings_the_copies_back():
    assert _poke_writes_through_filters_result(
        lambda fn: sel.register_function('T_POKE', 0, 0, fn)) == '1'


def test_a_function_defined_through_the_registry_brings_the_copies_back():
    # define() outside the manifest is the application's too (is_host_function).
    assert _poke_writes_through_filters_result(
        lambda fn: registry.define('T_POKE', 0, 0, fn=fn)) == '1'


def _deep_context():
    deep = Value.int(7)
    for _ in range(199):                     # 200 levels: fits at the root only
        deep = Value.list([deep])
    ctx = Value.none()
    ctx.set('D', deep)
    ctx.set('E', Value.list([deep]))
    return ctx


@pytest.mark.parametrize('src', [
    'MAP(LIST(1), D)', 'FILTER(E, TRUE)', 'SORT(E)', 'SORT_BY(E, 1)', 'TOP(E, 1)',
    'TOP_BY(E, 1, 1)', 'BUCKET(E, 1)', 'BUCKET(LIST(1), "a", D)', 'RECORD("a", D)',
    'LIST(D)', '(E, 0)', '(D, E)',
])
def test_a_write_free_program_still_refuses_what_the_copy_refused(src):
    program = sel.compile(src)
    with pytest.raises(SelError) as info:
        program.run(_deep_context())
    assert info.value.code == 'E_DEPTH'
    assert program._physical_plan()[1] is True


# --- depth: path + value, and where a constructor fails ------------------------

def deep(n):
    return 'A' + '[1]' * n + ' = 1;'


def test_assignment_counts_target_path_plus_value_depth():
    # A is 151 levels; stored 50 levels down that is 201.
    e = error(deep(150) + ' B' + '[1]' * 50 + ' = A; 7')
    assert e.code == 'E_DEPTH' and e.line == 1 and e.col > 0
    assert ev(deep(150) + ' B' + '[1]' * 49 + ' = A; 7').scalar == '7'


def test_a_200_level_value_fits_at_the_root_but_not_one_level_down():
    assert ev(deep(199) + ' B = A; 7').scalar == '7'
    assert error(deep(199) + ' B[1] = A; 7').code == 'E_DEPTH'


def test_a_constructor_refuses_at_its_own_node():
    for ctor in ('LIST(A)', 'RECORD("k", A)'):
        e = error(deep(199) + f' B = {ctor}; 7')
        assert e.code == 'E_DEPTH'
        assert e.col > 0                      # a position, not the 0:0 of a later walk
    assert ev(deep(198) + ' B = LIST(A); 7').scalar == '7'


def test_comma_with_a_scalar_bearing_operand_refuses_at_the_list():
    e = error('A = 0; ' + deep(199) + ' (A, 2); 7')
    assert e.code == 'E_DEPTH' and e.col > 0


# --- CEIL / FLOOR carry ---------------------------------------------------------

@pytest.mark.parametrize('src', [
    'CEIL(REPEAT("9", 1000000) & ".5")',
    'FLOOR("-" & REPEAT("9", 1000000) & ".5")',
])
def test_ceil_and_floor_carry_past_the_digit_cap_is_positioned(src):
    e = error(src)
    assert (e.code, e.line, e.col) == ('E_RANGE', 1, 1)


# --- the numeric core does not depend on the int<->str limit ----------

def test_import_does_not_override_the_deployers_limit():
    r = subprocess.run(
        [sys.executable, '-X', 'int_max_str_digits=5000', '-c',
         'import sys, sel; print(sys.get_int_max_str_digits())'],
        capture_output=True, text=True, env={'PYTHONPATH': str(_pkg_root())})
    assert r.stdout.strip() == '5000', r.stderr


def _pkg_root():
    import pathlib
    return pathlib.Path(sel.__file__).resolve().parent.parent


@pytest.mark.parametrize('limit', [640, 1000, 5000])
def test_long_numbers_work_under_any_limit_set_afterwards(limit):
    before = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(limit)
    try:
        assert ev('9' * 6000 + ' + 1').scalar == '1' + '0' * 6000
        assert ev('LEN(POWER(10, 5000))').scalar == '5001'
        assert len(ev('POWER(10, 5000) + 1').scalar) == 5001
        assert ev('CANON(1.5' + '0' * 3000 + ')').scalar == '1.5'
        assert ev('L = LIST(1); L["' + '9' * 5000 + '"] = 2; COUNT(L)').scalar == '2'
    finally:
        sys.set_int_max_str_digits(before)


# --- the Value API --------------------------------------------------------------

def test_setting_the_scalar_drops_the_cached_decimal():
    v = Value.text('5')
    assert v.as_decimal().digits == 5          # warms the cache
    v.scalar = '7'
    root = Value.from_native({'A': v})
    assert sel.compile('A + 1').run(root).scalar == '8'


@pytest.mark.parametrize('bad', [0, 0.0, '', b'', False, 1.5, 7, 'x', True])
def test_run_refuses_a_scalar_context(bad):
    with pytest.raises(SelError) as info:
        sel.compile('1').run(bad)
    assert info.value.code == 'E_BAD_ARG'


def test_run_accepts_none_dict_list_and_value():
    assert sel.compile('1').run().scalar == '1'
    assert sel.compile('1').run(None).scalar == '1'
    assert sel.compile('1').run({}).scalar == '1'
    assert sel.compile('COUNT(A)').run({'A': [1, 2]}).scalar == '2'
    assert sel.compile('1').run(Value.none()).scalar == '1'
    assert sel.compile('1').run([]).scalar == '1'
