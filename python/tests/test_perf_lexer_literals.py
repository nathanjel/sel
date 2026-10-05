"""Literal bodies are scanned a run at a time. Checked against the frozen earlier lexer
(fixtures/lexer_per_character.py, which reads them a character at a time) on sources made
mostly of literals: tokens, positions and errors must be identical."""
import importlib.util
import os
import random
import sys

import pytest
from sel import lexer as new
from sel.errors import SelError

OLD_PATH = os.path.join(os.path.dirname(__file__), 'fixtures', 'lexer_per_character.py')
spec = importlib.util.spec_from_file_location('sel_lexer_per_character_lit', OLD_PATH)
old = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = old
spec.loader.exec_module(old)


def dump(mod, src):
    try:
        return [(t.type, t.value, t.pos.line, t.pos.col, t.pos.offset) for t in mod.tokenize(src)]
    except SelError as e:
        return ('E', e.code, e.line, e.col, e.offset)


ALPHABET = ['a', 'bc', 'é', '😀', ' ', '\n', '\t', '"', "'", "''", '{', '}', '\\', '\\"', '\\{', '\\}',
            '\\n', '\\u{41}', '\\u{}', '\\x', '\\', '{1}', '{A}', '{"x"}', '#', '1', '+', '(', ')']


@pytest.mark.parametrize('seed', range(20))
def test_literal_heavy_sources_tokenise_identically(seed):
    rnd = random.Random(3100 + seed)
    for _ in range(600):
        src = ''.join(rnd.choice(ALPHABET) for _ in range(rnd.randrange(0, 18)))
        assert dump(new, src) == dump(old, src), repr(src)


@pytest.mark.parametrize('src', [
    '""', "''", '"abc"', "'abc'", '"a\\nb"', '"{1}"', '"x{1}y{2}z"', "'it''s'", "''''", '"\\""',
    '"unterminated', "'unterminated", '"{', '"a{1', '"\\', '"é😀é"', "'é😀é'", '"' + 'a' * 100000 + '"',
    "'" + 'b' * 100000 + "'", '"' + '{1}' * 500 + '"',
])
def test_fixed_literal_shapes_tokenise_identically(src):
    assert dump(new, src) == dump(old, src)
