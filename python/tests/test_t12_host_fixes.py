"""T12 host fixes: the flow-sensitive dependencies() rule (SPEC 8), compile() of
non-source input, and a registered host function reading an argument the call
does not have (SPEC 8.1). Each case is also a shared probe in tools/api-pins.txt."""
import copy
import pickle

import pytest
import sel
from sel import SelError, registry


def deps(src):
    return sel.compile(src).dependencies()


@pytest.mark.parametrize('src,want', [
    ('A + 1; A = 2', ['A']),
    ('X += 1', ['X']),
    ('A[1] += 1', ['A']),
    ('A[1] = 2', []),
    ('A = A + 1', ['A']),
    ('A = 1; A + B', ['B']),
    ('IF(X, A = 1, 0); A', ['A', 'X']),
    ('IF(X, A = 1, A = 2); A', ['X']),
    ('X AND (A = 1); A', ['A', 'X']),
    ('X OR (A = 1); A', ['A', 'X']),
    ('X ?? (A = 1); A', ['A', 'X']),
    ('X ??? (A = 1); A', ['A', 'X']),
    ('MAP(L, A = _); A', ['A', 'L']),
    ('COND(X, A = 1, Y, A = 2, A = 3); A', ['X', 'Y']),
    ('COND(X, A = 1, Y, A = 2, 0); A', ['A', 'X', 'Y']),
    ('LEFT("abc", (N = 2)); N', []),
    ('(A = 1, A)', []),
    ('A[I = 1] = 2; I', []),
])
def test_dependencies_are_flow_sensitive(src, want):
    assert deps(src) == want


def test_dependencies_keep_the_binder_and_depth_rules():
    assert deps('ALL(I, IT, IT > 0)') == ['I']
    with pytest.raises(SelError) as info:
        sel.compile('A' + '+A' * 300).dependencies()
    assert info.value.code == 'E_DEPTH'


@pytest.mark.parametrize('bad', [12, None, b'1 + 1', 1.5, ['1']])
def test_compile_of_non_source_is_e_bad_arg(bad):
    with pytest.raises(SelError) as info:
        sel.compile(bad)
    assert info.value.code == 'E_BAD_ARG'


@pytest.mark.parametrize('read', [
    lambda a, i: a.text(i), lambda a, i: a.val(i), lambda a, i: a.node(i),
    lambda a, i: a.pos_of(i), lambda a, i: a.symbol(i), lambda a, i: a.is_symbol(i),
])
@pytest.mark.parametrize('index', [3, -1])
def test_a_host_function_reading_a_missing_argument_is_e_bad_arg(read, index):
    # Every accessor, the binder-shape ones included: an IndexError (or Python's
    # negative indexing answering) would be the host leaking through SPEC 8.1.
    registry_name = 'T12_OOB'
    sel.register_function(registry_name, 1, 1, lambda a: (read(a, index), sel.Value.text('x'))[1])
    try:
        with pytest.raises(SelError) as info:
            sel.evaluate('T12_OOB(X)', {'X': '1'})
        assert info.value.code == 'E_BAD_ARG'
    finally:
        registry._table.pop(registry_name, None)
        registry._host.discard(registry_name)


def test_sel_error_round_trips_with_its_position():
    err = None
    try:
        sel.evaluate('1 +\n "a"')
    except SelError as e:
        err = e
    for clone in (pickle.loads(pickle.dumps(err)), copy.copy(err), copy.deepcopy(err)):
        assert (clone.code, clone.line, clone.col, clone.offset) == (err.code, err.line, err.col, err.offset)
        assert str(clone) == str(err)


def test_sql_error_round_trips_with_its_position():
    from sel.sql import Sql, SqlError
    with pytest.raises(SqlError) as info:
        Sql.translate(sel.compile('1 +\n A'), 'mariadb', {})
    err = info.value
    for clone in (pickle.loads(pickle.dumps(err)), copy.copy(err), copy.deepcopy(err)):
        assert type(clone) is SqlError
        assert (clone.code, clone.line, clone.col, clone.offset) == (err.code, err.line, err.col, err.offset)
        assert str(clone) == str(err)
