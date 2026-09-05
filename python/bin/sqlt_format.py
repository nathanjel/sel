"""The .sqlt case-file format, transcribed from php/bin/sqlt-format.php.

A second parser of a shared file is a second thing to drift, which is exactly
the defect docs/SQL-TESTING.md Class C is about -- so this one is a transcription
rather than a reimplementation, section names and error text included, and
python/bin/sqlt asserts that it loads the same number of cases PHP does.

sql/cases/*.sqlt is the contract both hosts are graded against: the cases assert
an exact string, so running them under two hosts is what makes "the same SQL
everywhere" a measurement rather than an intention.
"""

from __future__ import annotations

import json
import os
from typing import Any

SUITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'sql', 'cases')

SECTIONS = ('dialect', 'register', 'bindings', 'options', 'as', 'mode',
            'source', 'expect', 'params', 'error', 'throws')


class SuiteError(Exception):
    """A malformed case file. Not a failing case -- a suite that cannot be read."""


def parse_sqlt(text: str, file: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    cur = -1
    section: str | None = None
    for idx, line in enumerate(text.split('\n')):
        at = f'{file}:{idx + 1}'
        if line.startswith('### '):
            rest = line[4:]
            if not rest.startswith('name:'):
                raise SuiteError(f'{at}: malformed case header')
            name = rest[5:].strip()
            # `\S+` in the PHP: exactly one non-space token, nothing after it.
            if name == '' or len(name.split()) != 1:
                raise SuiteError(f'{at}: malformed case header')
            case: dict[str, Any] = {'name': name, 'at': at}
            for s in SECTIONS:
                case[s] = None
            cases.append(case)
            cur = len(cases) - 1
            section = None
            continue
        if line == '===':
            cur = -1
            section = None
            continue
        if line.startswith('--- '):
            if cur < 0:
                raise SuiteError(f'{at}: section outside a case')
            section = line[4:].strip()
            if section in SECTIONS:
                cases[cur][section] = []
            elif section != 'note':
                raise SuiteError(f'{at}: unknown section {section}')
            continue
        if cur < 0 or section is None or section == 'note':
            continue                               # header text, ignored
        cases[cur][section].append(line)

    for c in cases:
        for s in SECTIONS:
            if c[s] is not None:
                c[s] = '\n'.join(c[s]).strip()
        if c['source'] is None:
            raise SuiteError(f"{c['at']}: case {c['name']} has no --- source")
        outcomes = [x for x in (c['expect'], c['error'], c['throws']) if x is not None]
        if len(outcomes) != 1:
            raise SuiteError(f"{c['at']}: case {c['name']} needs exactly one of "
                             '--- expect, --- error and --- throws')
    return cases


def decode_json(text: str | None, what: str, at: str) -> Any:
    if text is None or text == '':
        return {}
    try:
        v = json.loads(text)
    except ValueError:
        raise SuiteError(f'{at}: --- {what} is not a JSON object') from None
    # PHP checks is_array, which is true for both a JSON object and a JSON
    # array; `--- params` is an array and the rest are objects.
    if not isinstance(v, (dict, list)):
        raise SuiteError(f'{at}: --- {what} is not a JSON object')
    return v


def load_suite() -> list[dict[str, Any]]:
    """Every case in sql/cases, in file then source order."""
    out: list[dict[str, Any]] = []
    for name in sorted(os.listdir(SUITE)):
        if not name.endswith('.sqlt'):
            continue
        path = os.path.join(SUITE, name)
        with open(path, encoding='utf-8') as fh:
            out.extend(parse_sqlt(fh.read(), name))
    return out
