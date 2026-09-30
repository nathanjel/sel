"""PY-P24: the small value-level overheads. is_vacuous must not format a number, the dense
list index hashes come from a table that never changes what is hashed, and a refused
size argument must not build the message from a million-digit integer."""
import time

import pytest

import sel
from sel import Value, SelError
from sel import decimal as D
from sel import value as V


def test_is_vacuous_of_a_number_never_formats_it():
    big = Value.num(D.make(False, 10 ** 999_998, 0))
    assert big._scalar is None and big._dec_val is not None       # only the decimal is built
    t = time.process_time()
    assert big.is_vacuous() is False
    assert time.process_time() - t < 0.2                           # formatting it took ~0.9 s
    assert big._scalar is None                                     # and it is still unformatted


@pytest.mark.parametrize('make,want', [
    (lambda: Value.text(''), True), (lambda: Value.text('  \t\r\n'), True),
    (lambda: Value.text('a'), False), (lambda: Value.text(' a '), False),
    (lambda: Value.int(0), False), (lambda: Value.int(-7), False),
    (lambda: Value.num(D.parse('0.50', None)), False),
    (lambda: Value.bool(False), False), (lambda: Value.from_native(None), True),
    (lambda: Value.from_native([]), True),
])
def test_is_vacuous_answers_are_unchanged(make, want):
    assert make().is_vacuous() is want


def reference_hash(value, depth=1):
    """structural_hash with hash(str(i)) computed per element, as before the table."""
    if value.size() == 0 or not (value.is_list and value.storage is not None
                                 and value.list_keys is None and value.shape is None):
        return V._structural_hash_at(value, depth)
    k = value.kind
    h = {V.TEXT: lambda: hash(value.scalar) ^ 1000003, V.BOOL: lambda: 12345 if value.scalar else 67890,
         V.BIN: lambda: hash(value.scalar) ^ 2000003}.get(k, lambda: 0)()
    for i, child in enumerate(value.storage, 1):
        h = ((h * 1000003) ^ hash(str(i)) ^ reference_hash(child, depth + 1)) & 0xffffffffffffffff
    return h


@pytest.mark.parametrize('n', [1, 2, 8, 4095, 4096, 4097, 5000])
def test_structural_hash_of_a_dense_list_matches_the_per_element_hash(n):
    v = Value.from_native(list(range(n)))
    assert V.structural_hash(v) == reference_hash(v)


def test_the_table_grows_by_replacement_and_keeps_its_prefix(monkeypatch):
    monkeypatch.setattr(V, '_INDEX_HASHES', [])       # earlier tests may have grown it already
    small = Value.from_native(list(range(10)))
    V.structural_hash(small)
    t1 = V._INDEX_HASHES
    snapshot = list(t1)
    V.structural_hash(Value.from_native(list(range(100))))
    t2 = V._INDEX_HASHES
    assert t2 is not t1 and t2[:len(snapshot)] == snapshot
    assert t1 == snapshot                                          # the old table was not touched
    assert len(t2) >= 100 and len(V._INDEX_HASHES) <= V._INDEX_HASH_LIMIT


def test_keyed_lists_and_records_hash_as_before():
    a = Value.from_native({'a': 1, 'b': [1, 2, 3]})
    b = Value.from_native({'a': 1, 'b': [1, 2, 3]})
    assert V.structural_hash(a) == V.structural_hash(b)
    assert V.structural_hash(a) != V.structural_hash(Value.from_native({'a': 1, 'b': [1, 2, 4]}))


def test_a_huge_size_argument_is_a_sel_error_not_a_value_error():
    for src in ('ROUND(1, 1' + '0' * 5000 + ')', 'POWER(2, 1' + '0' * 5000 + ')'):
        with pytest.raises(SelError) as info:
            sel.evaluate(src)
        assert info.value.code == 'E_RANGE'
        assert len(str(info.value)) < 200                          # not the integer itself


def test_a_small_refused_size_argument_keeps_its_message():
    with pytest.raises(SelError) as info:
        sel.evaluate('ROUND(1, 1000001)')
    assert info.value.code == 'E_RANGE' and '1000001' in str(info.value)
