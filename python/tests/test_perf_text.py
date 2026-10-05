"""UPPER/LOWER and the TRIM family use str methods and a translate table;
they must give exactly what the per-character loops did (kept here as the
reference), including for text where Python's own case mapping would differ."""
import random

import sel

SPACE = ' \t\r\n'


def ref_trim(s, left, right):
    a, b = 0, len(s)
    if left:
        while a < b and s[a] in SPACE:
            a += 1
    if right:
        while b > a and s[b - 1] in SPACE:
            b -= 1
    return s[a:b]


def ref_case(s, up):
    out = []
    for ch in s:
        c = ord(ch)
        if up and 0x61 <= c <= 0x7A:
            out.append(chr(c - 32))
        elif not up and 0x41 <= c <= 0x5A:
            out.append(chr(c + 32))
        else:
            out.append(ch)
    return ''.join(out)


# Characters whose Unicode case mapping is NOT the ASCII rule (length changes,
# non-ASCII targets): the translate table must leave all of them alone.
TRICKY = ['ß', 'ﬁ', 'İ', 'ı', 'ſ', 'K', 'ǆ', 'ᾳ', 'é', 'É', ' ', ' ', '\u0085', '﻿']
ALPHABET = list('abzAZ09 \t\r\n_-') + TRICKY


def run(fn, s):
    return sel.evaluate(f'{fn}(S)', {'S': s}).scalar


def test_case_matches_the_reference_on_random_text():
    rnd = random.Random(20260930)
    for _ in range(3000):
        s = ''.join(rnd.choice(ALPHABET) for _ in range(rnd.randrange(0, 40)))
        assert run('UPPER', s) == ref_case(s, True), repr(s)
        assert run('LOWER', s) == ref_case(s, False), repr(s)


def test_case_never_applies_the_unicode_mapping():
    assert run('UPPER', 'straße') == 'STRAßE'
    assert run('UPPER', 'ﬁn') == 'ﬁN'
    assert run('LOWER', 'İSTANBUL') == 'İstanbul'
    assert run('LOWER', 'KELVIN') == 'Kelvin'


def test_trim_matches_the_reference_on_random_text():
    rnd = random.Random(7)
    for _ in range(3000):
        s = ''.join(rnd.choice(ALPHABET) for _ in range(rnd.randrange(0, 30)))
        assert run('TRIM', s) == ref_trim(s, True, True), repr(s)
        assert run('LTRIM', s) == ref_trim(s, True, False), repr(s)
        assert run('RTRIM', s) == ref_trim(s, False, True), repr(s)


def test_trim_strips_only_the_four_characters():
    s = '   \t x \u0085﻿\r\n'
    assert run('TRIM', s) == '   \t x \u0085﻿'[0:0] + s.strip(SPACE)
    assert run('TRIM', s).startswith(' ')
    assert run('TRIM', s).endswith('﻿')
