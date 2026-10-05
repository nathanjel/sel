"""The lexer's set/regex scanning must tokenise exactly as the original
per-character loops did -- tokens, positions and errors -- checked against the
pre-change lexer kept as a fixture."""
import importlib.util
import os
import random
import sys

import pytest
from sel import lexer as new
from sel.errors import SelError

HERE = os.path.dirname(__file__)
OLD_PATH = os.path.join(HERE, 'fixtures', 'lexer_per_character.py')


def load_old():
    spec = importlib.util.spec_from_file_location('sel_lexer_per_character', OLD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod        # @dataclass resolves its module by name
    spec.loader.exec_module(mod)
    return mod


old = load_old()


def dump(mod, src):
    try:
        return [(t.type, t.value, t.pos.line, t.pos.col, t.pos.offset) for t in mod.tokenize(src)]
    except SelError as e:
        return ('E', e.code, e.line, e.col, e.offset)


ALPHABET = ['a', 'Z', '_', '7', '0', '.', ' ', '\t', '\r', '\n', '#', '"', "'", '{', '}', '(', ')',
            '[', ']', ',', ';', '+', '-', '*', '/', '%', '&', '=', '<', '>', '!', '$', '?', '@',
            'é', '😀', '\\', '1.5', '??', '???', '$==', '.>', '<=', 'TRUE', 'x1', '0.']


@pytest.mark.parametrize('seed', range(30))
def test_random_sources_tokenise_identically(seed):
    r = random.Random(seed)
    for _ in range(300):
        src = ''.join(r.choice(ALPHABET) for _ in range(r.randrange(1, 30)))
        assert dump(new, src) == dump(old, src), repr(src)


def test_every_operator_is_found_by_the_set_lookup_as_by_the_scan():
    for op in new.OPERATORS:
        for tail in ('', ' ', 'a', '=', '>', '?', '$'):
            src = op + tail
            assert dump(new, src) == dump(old, src), repr(src)


def test_no_operator_is_a_proper_prefix_of_one_listed_after_it():
    ops = new.OPERATORS
    for i, a in enumerate(ops):
        for b in ops[i + 1:]:
            assert not (len(b) > len(a) and b.startswith(a)), (a, b)


def test_line_starts_and_positions_agree_with_the_old_lexer():
    src = 'a\n\nbb\r\nccc\n' + 'é😀\n' + 'x'
    assert dump(new, src) == dump(old, src)
