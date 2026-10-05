"""Refusal messages are human text, not contract (codes and positions are), but
they must not describe a program that was not written: a mixed-kind comparison
names the two kinds it actually saw, and says what SEL does with them -- EQL and
IN answer FALSE across kinds, the `$` family compares bytes."""
import pytest

import sel
from sel.sql import Binding, Sql, SqlError

BINDINGS = {'B': Binding.column('b', 't', 'BIN'), 'T': Binding.column('t', 't', 'TEXT'),
            'F': Binding.column('f', 't', 'BOOL'), 'N': Binding.column('n', 't', 'NUM')}


@pytest.mark.parametrize('src, kinds, says', [
    ('B EQL T', ('BIN', 'TEXT'), 'FALSE'),
    ('T EQL B', ('TEXT', 'BIN'), 'FALSE'),
    ('TO_UTF8(T) EQL T', ('BIN', 'TEXT'), 'FALSE'),
    ('N EQL F', ('NUM', 'BOOL'), 'FALSE'),
    ('B $== T', ('BIN', 'TEXT'), 'byte for byte'),
    ('N $< B', ('NUM', 'BIN'), 'byte for byte'),
])
def test_a_mixed_kind_comparison_names_both_kinds(src, kinds, says):
    with pytest.raises(SqlError) as info:
        Sql.translate(sel.compile(src), 'postgresql', BINDINGS)
    assert info.value.code == 'E_SQL_SHAPE'
    assert f'compares a {kinds[0]} with a {kinds[1]}, which SEL ' in info.value.message
    assert says in info.value.message
