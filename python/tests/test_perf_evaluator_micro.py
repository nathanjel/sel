"""A text literal skips the validating constructor when it is ASCII, and BAND/BOR/BXOR
use integer arithmetic above a size threshold. Answers, errors and positions are unchanged."""
import os
import random

import pytest

import sel
from sel import SelError, Value
from sel import eval as E
from sel.errors import Pos
from sel.parser import Node


def byte_loop(op, a, b):
    f = {'BAND': lambda x, y: x & y, 'BOR': lambda x, y: x | y, 'BXOR': lambda x, y: x ^ y}[op]
    return bytes(f(x, y) for x, y in zip(a, b))


@pytest.mark.parametrize('size', [0, 1, 2, 7, 8, 31, 32, 63, 64, 65, 66, 127, 128, 1000, 4097])
@pytest.mark.parametrize('op', ['BAND', 'BOR', 'BXOR'])
def test_bitwise_matches_the_byte_loop_around_the_threshold(size, op):
    rnd = random.Random(size * 3 + len(op))
    for a, b in ((os.urandom(size), os.urandom(size)), (bytes(size), bytes([255]) * size),
                 (bytes([255]) * size, bytes([255]) * size), (bytes(size), bytes(size)),
                 (bytes([rnd.randrange(256)]) * size, os.urandom(size))):
        assert E._bitwise(op, a, b, None).scalar == byte_loop(op, a, b)


def test_leading_zero_bytes_survive_the_integer_path():
    a = bytes([0, 0, 0] + [7] * 100)
    b = bytes([0, 0, 1] + [3] * 100)
    r = E._bitwise('BOR', a, b, None).scalar
    assert len(r) == 103 and r[:3] == bytes([0, 0, 1])


def test_length_mismatch_is_still_an_error_at_the_call():
    with pytest.raises(SelError) as info:
        sel.evaluate('FROM_HEX("0f") BAND FROM_HEX("0f3c")')
    assert info.value.code == 'E_LEN_MISMATCH'


def test_bitwise_through_the_language_on_large_operands():
    hexa = '0f' * 5000
    src = 'TO_HEX(FROM_HEX("%s") BXOR FROM_HEX("%s"))' % (hexa, '3c' * 5000)
    assert sel.evaluate(src).scalar == '33' * 5000


def test_an_ascii_text_literal_is_an_independent_value_each_time():
    program = sel.compile('MAP(LIST(1, 2), "x")')
    out = program.run(Value.from_native({}))
    a, b = [v for _, v in sel.value.elements(out)]
    assert a.scalar == b.scalar == 'x' and a is not b


@pytest.mark.parametrize('text', ['', 'plain', 'zażółć', '😀', 'tab\there'])
def test_text_literals_evaluate_to_themselves(text):
    escaped = text.replace('\\', '\\\\').replace('"', '\\"').replace('\t', '\\t')
    assert sel.evaluate('"%s"' % escaped).scalar == text


def test_a_hand_built_literal_node_with_a_lone_surrogate_is_still_refused():
    node = Node('text', Pos(1, 1, 0), v='a\ud800')
    program = sel.Program('x', node)
    with pytest.raises(SelError) as info:
        program.run(Value.from_native({}))
    assert info.value.code == 'E_UTF8'


def test_a_hand_built_literal_node_that_is_not_a_str_is_still_refused():
    node = Node('text', Pos(1, 1, 0), v=b'bytes')
    program = sel.Program('x', node)
    with pytest.raises(SelError) as info:
        program.run(Value.from_native({}))
    assert info.value.code == 'E_BAD_ARG'
