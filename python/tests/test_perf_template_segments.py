"""Emit.fill scans a template once per distinct string (the segment cache). The part
list it produces must be exactly what the character-by-character scan produced, for every
template shape including unbalanced and doubled braces."""
import random

import pytest

from sel.errors import SelError
from sel.sql.errors import SqlError
from sel.sql import emit as E
from sel.sql.fragment import Fragment


def reference_fill(tpl, args):
    """The scan fill() used before the cache, for templates without lexical references."""
    parts = []

    def push(s):
        if s == '':
            return
        if parts and isinstance(parts[-1], str):
            parts[-1] += s
        else:
            parts.append(s)

    def splice(f):
        for p in f.parts:
            push(p) if isinstance(p, str) else parts.append(p)

    def join(subset):
        first = True
        for f in subset:
            if not first:
                push(', ')
            first = False
            splice(f)

    i, n = 0, len(tpl)
    while i < n:
        if tpl[i] == '{' and i + 1 < n and tpl[i + 1] == '{':
            push('{'); i += 2; continue
        if tpl[i] == '}' and i + 1 < n and tpl[i + 1] == '}':
            push('}'); i += 2; continue
        if tpl[i] != '{':
            push(tpl[i]); i += 1; continue
        end = tpl.find('}', i)
        if end == -1:
            push(tpl[i:]); break
        slot = tpl[i + 1:end]
        i = end + 1
        if slot == '*':
            join(args); continue
        if slot.endswith(':'):
            frm = E._slot_index(slot[:-1])
            if frm is not None:
                join(args[frm:]); continue
        k = E._slot_index(slot)
        if k is not None:
            splice(args[k]); continue
        raise AssertionError('lexical slot in a reference template: ' + slot)
    return parts


def frag(text):
    return Fragment([text], 'TEXT', 'postgresql')


ARGS = [frag('A'), frag('B'), frag('C')]


@pytest.mark.parametrize('tpl', [
    '', 'plain', '{0}', '({0} + {1})', 'f({*})', 'g({1:})', '{{literal}}', '{{{0}}}', '}}{{', 'a}b', '}', '{',
    'open {0', 'x{{y}}z{2}w', '{0}{1}{2}', '{2} and {0}', '} lone', 'CAST({0} AS TEXT) COLLATE "C"',
    'a{{', '}}b', '{{}}', '{0}}}', '{{{{', 'trailing {', 'two {0} {{ {1} }} end',
])
def test_fill_matches_the_character_scan_on_fixed_templates(tpl):
    em = E.Emit('postgresql')
    assert em.fill(tpl, ARGS) == reference_fill(tpl, ARGS)


def test_fill_matches_the_character_scan_on_random_templates():
    rnd = random.Random(2828)
    alphabet = ['a', 'b', ' ', '{', '}', '{{', '}}', '{0}', '{1}', '{2}', '{*}', '{1:}', '(', ')', ',', '"', "'"]
    em = E.Emit('postgresql')
    for _ in range(4000):
        tpl = ''.join(rnd.choice(alphabet) for _ in range(rnd.randrange(0, 14)))
        try:
            want = reference_fill(tpl, ARGS)
        except (AssertionError, IndexError):
            continue                      # a lexical slot or a missing argument: not in scope here
        try:
            got = em.fill(tpl, ARGS)
        except SelError:
            continue
        assert got == want, tpl


def test_a_missing_argument_is_still_refused():
    em = E.Emit('postgresql')
    with pytest.raises(SqlError) as info:
        em.fill('{5}', ARGS)
    assert info.value.code == 'E_SQL_UNSUPPORTED'


def test_the_segment_cache_is_bounded_and_repeatable(monkeypatch):
    monkeypatch.setattr(E, '_SEGMENTS_MAX', 8)
    E._SEGMENTS.clear()
    for i in range(50):
        assert E._segments('x%d {0}' % i) == ((False, 'x%d ' % i), (True, '0'))
    assert len(E._SEGMENTS) <= 8
    assert E._segments('x3 {0}') == ((False, 'x3 '), (True, '0'))        # a miss rescans, same answer
