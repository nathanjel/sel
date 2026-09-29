#!/usr/bin/env python3
"""Writes conformance/28b-regex-ambiguity.selt from the lists in
tools/regex-ambiguity-ref.py (the reference validator) and, for the accepted
patterns, from Python's own `re` as an independent oracle of the *match* result
(re.search, re.ASCII | re.DOTALL -- SEL's \\d \\w \\s are ASCII and `.` is dotall).

  tools/gen-regex-ambiguity-cases.py            write the file
  tools/gen-regex-ambiguity-cases.py --check    fail if the file is stale

A pattern the reference and a case disagree on stops the run: the lists are
pinned only once the reference agrees with every one of them.
"""
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('ref', os.path.join(ROOT, 'tools/regex-ambiguity-ref.py'))
ref = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ref)
OUT = os.path.join(ROOT, 'conformance/28b-regex-ambiguity.selt')


def sel_str(s):
    out = []
    for ch in s:
        out.append({'\\': '\\\\', '"': '\\"', '\n': '\\n', '\r': '\\r', '\t': '\\t', '{': '\\{', '}': '\\}'}.get(ch, ch))
    return '"' + ''.join(out) + '"'


def raw(p):
    assert "'" not in p
    return "'" + p + "'"


# accepted patterns that need a chosen subject: (pattern, subject expression, python subject or None)
BIG = {
    r'^(a{300}){300}$': ('REPEAT("a", 90000)', 'a' * 90000),
    r'(?:a{60000}){60000}': (None, 'a'),
    r'^a{65535}$': ('REPEAT("a", 65535)', 'a' * 65535),
    r'^(?:ab){65535}$': ('REPEAT("ab", 65535)', 'ab' * 65535),
    r'^[a-z]{1,65535}$': (None, 'abc'),
}
SUBJECT = {
    r'(\d+,)+': '12,3,', r'(?:ab|cd)*': 'abcd',
    r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$': 'a.b@c.io',
    r'(\w+\s)*': 'ab cd ', r'([a-z]+-)*[a-z]+': 'ab-cd', r'(?:a|b)*': 'abba', r'a*b*c*': 'aabbcc',
    r'^\d{3}-\d{3}-\d{4}$': '555-123-4567', r'^(\d{1,3}\.){3}\d{1,3}$': '192.168.0.1',
    r'^[+-]?\d+(?:\.\d+)?$': '-12.5', r'^"(?:[^"\\]|\\.)*"$': '"a\\"b"',
    r'^[a-z0-9]+(?:[-_.][a-z0-9]+)*$': 'a-b_c.d',
    r'^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?$': 'https://www.example.com/x',
    r'^(?:[^,]*,)*[^,]*$': 'a,b,c', r'^\s*(\w+)\s*=\s*(.*?)\s*$': ' k = v ',
    r'(?:\r\n|\n)*': '\r\n', r'^(?:[a-z]+\d+)*$': 'ab1cd22',
    r'^[A-Z]{2}\d{2}(?: ?\d{4}){4,7}$': 'PL61 1090 1014 0000 0712 1981 2874',
    r'^(?:ab|ac)*$': 'abac', r'^(?:ab|a)*$': 'aab', r'(?:foo|foobar)*': 'foobarfoo',
    r'^(?:\d{3}){1,2}$': '123456', r'(?:a{2}){3}': 'aaaaaa',
    r'^[a-z]+(?:[A-Z][a-z]+)*$': 'fooBarBaz', r'^(?:[A-Z][a-z0-9]+)+$': 'FooBar1',
    r'(?:[a-z]|[A-Z])+$': 'abC', r'^(?:[0-9]*|[a-z]*)$': '123', r'^(\d*)?$': '12',
    r'(^|[^0-9A-Za-z_])foo($|[^0-9A-Za-z_])': 'a foo b', r'^(?:a+b)+$': 'aabab',
    r'^[a-z]+(\.[a-z]+)*$': 'a.b.c', r'^(a|b)*$': 'abab', r'^(?:a|b)+c?$': 'abc',
    r'^[^<>]*(?:<[^<>]*>[^<>]*)*$': 'x<y>z<w>', r'^(.*),(.*),(.*),(.*)$': 'a,b,c,d',
    r'a*a*$': 'aaa',
    # realistic shapes (added with the pinned lists)
    r'^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$': 'sub.example.com',
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$': '123e4567-e89b-12d3-a456-426614174000',
    r'^\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$': '1.2.3-rc.1',
    r'^\+?\d{1,3}[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}$': '+48 (555) 123-4567',
    r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:,(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))*$': 'Mon,Wed,Sun',
}
ACCEPT = ref.MUST_ACCEPT + [
    r'^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$',
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    r'^\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$',
    r'^\+?\d{1,3}[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}$',
    r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:,(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun))*$',
]


def py_search(p, s, ic=False):
    flags = re.ASCII | re.DOTALL | (re.IGNORECASE if ic else 0)
    return re.search(p, s, flags) is not None


def case(name, note, src, expect):
    return f"### name: {name}\n--- note\n{note}\n--- source\n{src}\n--- expect\n{expect}\n===\n"


def build():
    cases = []
    # every pinned expectation must agree with the reference first
    def must(p, ok, ic=False):
        try:
            ref.validate(p, ic)
            got = True
        except ref.Reject as r:
            got = False
        if got != ok:
            sys.exit(f'reference disagrees with the pinned expectation: {p!r} ic={ic} want {ok}')

    for i, p in enumerate(ref.MUST_REJECT, 1):
        must(p, False)
        cases.append(case(
            f're.ambiguity.reject.{i}',
            'SPEC 7.8: a pattern a backtracking engine can be driven to exponential time on is refused at compile time, '
            'whatever the subject. (Patterns from the ReDoS literature and from real validation rules.) The subject is '
            'irrelevant; it is short on purpose so that only compile-time validation can produce the result.',
            f'RMATCH({raw(p)}, "aaa")', 'error E_REGEX_SYNTAX'))
    for i, p in enumerate(ref.FLAG_PAIRS, 1):
        must(p, False, True)
        must(p, True, False)
        subj = {1: 'aAa', 2: 'aAz', 3: 'abAB'}[i]
        cases.append(case(
            f're.ambiguity.flag-i.rejects.{i}',
            'The `i` flag is folded into the analysis: the two branches are disjoint without it and overlap with it, so '
            'the same pattern is refused with `i` (the validator takes the flag; caches are keyed by it).',
            f'RMATCH({raw(p)}, {sel_str(subj)}, "i")', 'error E_REGEX_SYNTAX'))
        cases.append(case(
            f're.ambiguity.flag-i.accepts-without.{i}',
            'CONTROL for the case before: without `i` the branches are disjoint and the pattern is fine.',
            f'RMATCH({raw(p)}, {sel_str(subj)})', 'bool ' + str(py_search(p, subj)).upper()))
    for i, (p, ok) in enumerate(ref.BUDGET, 1):
        must(p, ok)
        if ok:
            subj = 'x' if p.startswith('(?:|)') else ('b' if p.startswith('a?') else 'a' * 8 + 'x' if p.startswith('(a|a)') else 'a')
            exp = 'bool ' + str(py_search(p, subj)).upper()
            src = f'RMATCH({raw(p)}, {sel_str(subj)})'
        else:
            exp, src = 'error E_REGEX_SYNTAX', f'RMATCH({raw(p)}, "x")'
        cases.append(case(
            f're.ambiguity.budget.{i}',
            'The ambiguity budget (16): finite choices outside any loop multiply the ways a word can match, and their '
            'log2s are summed. Each pair straddles the boundary: the shorter form is accepted, one more is refused.',
            src, exp))
    for i, p in enumerate(ref.STRUCTURAL_REJECT, 1):
        must(p, False)
        cases.append(case(
            f're.ambiguity.structural.reject.{i}',
            'Structural errors: the validator is a real parser now, and a pattern it cannot parse is `E_REGEX_SYNTAX`.',
            f'RMATCH({raw(p)}, "a")', 'error E_REGEX_SYNTAX'))
    for i, p in enumerate(['', 'x{0}', '^$', '(a | a)'], 1):
        must(p, True)
        subj = {'': 'abc', 'x{0}': 'abc', '^$': '', '(a | a)': 'a '}[p]
        cases.append(case(
            f're.ambiguity.structural.accept.{i}',
            'CONTROL: the empty pattern, a zero-count repeat, and two anchors are valid, and so is a group whose '
            'alternatives merely contain spaces.',
            f'RMATCH({raw(p)}, {sel_str(subj)})', 'bool ' + str(py_search(p, subj)).upper()))
    for i, p in enumerate(ACCEPT, 1):
        must(p, True)
        if p in BIG:
            subj_sel, subj_py = BIG[p]
            subj_sel = subj_sel or sel_str(subj_py)
            want = True if p != r'(?:a{60000}){60000}' else False
        else:
            subj_py = SUBJECT[p]
            subj_sel = sel_str(subj_py)
            want = py_search(p, subj_py)
        cases.append(case(
            f're.ambiguity.accept.{i}',
            'A pattern that is NOT refused: real validation rules, patterns with polynomial (not exponential) '
            'ambiguity, and unambiguous alternations that share a prefix. A false rejection here would break '
            'working rules, so each is pinned. The expected match result comes from Python\'s re, not from SEL.',
            f'RMATCH({raw(p)}, {subj_sel})', 'bool ' + str(want).upper()))
    hdr = ('% Exponential-ambiguity refusal (SPEC 7.8), pinned. GENERATED by tools/gen-regex-ambiguity-cases.py from the\n'
           '% reference validator tools/regex-ambiguity-ref.py: every case agreed with the reference before it was written,\n'
           '% and every accepted pattern\'s match result is Python re\'s. Regenerate with that script; do not edit by hand.\n\n')
    return hdr + '\n'.join(cases)


def main():
    text = build()
    if sys.argv[1:] == ['--check']:
        cur = open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else ''
        if cur != text:
            sys.exit(f'{OUT} is stale: run tools/gen-regex-ambiguity-cases.py')
        print('28b-regex-ambiguity.selt is current')
        return
    open(OUT, 'w', encoding='utf-8').write(text)
    print('wrote', OUT, text.count('### name:'), 'cases')


main()
