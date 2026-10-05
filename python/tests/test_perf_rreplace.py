"""PY-P13: RREPLACE parses the replacement once and inlines the match loop. It must
give exactly what the per-match `_expand` and the generator loop gave: same text,
same errors (E_BAD_ARG for a group past the pattern's, only when a match occurs),
same zero-width advancement, same length cap."""
import random

import pytest
import sel
from sel.builtins import regex as R
from sel.errors import SelError


def ref_expand(repl, m, rx, original):
    out = []
    i = 0
    while i < len(repl):
        if repl[i] != '$':
            out.append(repl[i])
            i += 1
            continue
        nxt = repl[i + 1] if i + 1 < len(repl) else ''
        if nxt == '$':
            out.append('$')
            i += 2
            continue
        if nxt and '0' <= nxt <= '9':
            g = int(nxt)
            if g > rx.groups:
                raise ValueError('bad group')
            val = R._group_text(m, g, original)
            out.append(val if val is not None else '')
            i += 2
            continue
        out.append('$')
        i += 1
    return ''.join(out)


def matches(rx, subject):
    """The match loop every host runs, rather than finditer, so zero-width
    advancement is the same everywhere: after an empty match, a whole code point."""
    pos = 0
    n = len(subject)
    while pos <= n:
        m = rx.search(subject, pos)
        if m is None:
            return
        yield m
        pos = m.end() + 1 if m.end() == m.start() else m.end()


def ref_rreplace(pattern, repl, subject):
    rx, ic = R._compile(pattern, '', None, None)
    hay = R._fold_subject(subject) if ic else subject
    out, last = [], 0
    for m in matches(rx, hay):
        out.append(subject[last:m.start()])
        out.append(ref_expand(repl, m, rx, subject))
        last = m.end()
    out.append(subject[last:])
    return ''.join(out)


PATTERNS = ['a', 'a*', '', '(a)(b)?', '(a+)b(c*)', '[ab]', 'b*?', '(x)|(a)', '^a', 'a$', '(a|b)+c?', '.']
REPLS = ['', 'x', '$0', '$1', '$2', '[$0]', '$$', '$$0', '$', 'a$', '$a', '$&', '$`', '\\1', '$1$2', '$9', '$00', 'é$0é']
ALPHA = list('abcx') + ['é', '\n', ' ']


def test_matches_the_reference_on_random_inputs():
    rnd = random.Random(20260930)
    checked = 0
    for _ in range(4000):
        pat, repl = rnd.choice(PATTERNS), rnd.choice(REPLS)
        subj = ''.join(rnd.choice(ALPHA) for _ in range(rnd.randrange(0, 14)))
        try:
            want = ref_rreplace(pat, repl, subj)
        except ValueError:
            want = 'E_BAD_ARG'
        try:
            got = sel.evaluate('RREPLACE(P, R, S)', {'P': pat, 'R': repl, 'S': subj}).scalar
        except SelError as e:
            got = e.code
        assert got == want, (pat, repl, subj)
        checked += 1
    assert checked == 4000


def test_a_bad_group_is_an_error_only_when_something_matches():
    assert sel.evaluate('RREPLACE("z", "$5", "abc")').scalar == 'abc'
    with pytest.raises(SelError) as e:
        sel.evaluate('RREPLACE("a", "$5", "abc")')
    assert e.value.code == 'E_BAD_ARG'
    assert (e.value.line, e.value.col) == (1, 15)


def test_the_length_cap_still_applies():
    with pytest.raises(SelError) as e:
        sel.evaluate('RREPLACE("a", REPEAT("b", 200000), REPEAT("a", 200))')
    assert e.value.code == 'E_RANGE'
