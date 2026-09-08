#!/usr/bin/env python3
"""Conformance runner. The suite in conformance/ is normative; this program has
no opinions of its own beyond the file format in conformance/README.md.

Note that expectation strings are unescaped by this file's own tiny escape
reader, not by SEL's lexer — the suite must not validate the lexer with the
lexer.
"""

import os
import re
import sys

# Prefer an *installed* sel over the source tree, so the python-wheel
# implementation in tools/impls.sh actually exercises the built package rather
# than silently re-testing python/sel through a path insert. Falls back to the
# source tree when nothing is installed, which is how the plain `python`
# implementation and a bare checkout run.
try:
    import sel as _sel_probe                                    # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from sel import SelError, Value, compile as sel_compile   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.abspath(os.path.join(HERE, '..', '..', 'conformance'))

_HEADER = re.compile(r'^###\s+name:\s*(\S+)\s*$')
_ERR = re.compile(r'^(\S+)(?:\s+at\s+(\d+):(\d+))?$')


# --- .selt parsing ----------------------------------------------------------

def _trim_ws(t: str) -> str:
    """SEL's four whitespace characters and nothing else.

    ``str.strip()`` removes every Unicode whitespace character, which is a wider
    set than the other readers use -- NBSP and the line separators among them --
    so a case whose source began with one would be a different program in this
    host than in C++ or Lisp. The same class of bug as JS's ``trim()`` deleting a
    byte-order mark, which is what found this. conformance/README.md is
    normative for the set.
    """
    return t.strip(' \t\r\n')


def parse_selt(text, file):
    cases = []
    cur = None
    section = None
    # .split('\n'), never .splitlines(): splitlines also breaks on \v, \f,
    # \x1c-\x1e, U+0085, U+2028 and U+2029, none of which end a line here — and
    # the suite deliberately contains such characters inside test sources.
    for idx, line in enumerate(text.split('\n')):
        at = f'{file}:{idx + 1}'
        if line.startswith('### '):
            m = _HEADER.match(line)
            if not m:
                raise RuntimeError(f'{at}: malformed case header')
            cur = {'name': m.group(1), 'at': at,
                   'setup': None, 'source': None, 'expect': None}
            cases.append(cur)
            section = None
            continue
        if line == '===':
            cur = None
            section = None
            continue
        if line.startswith('--- '):
            if cur is None:
                raise RuntimeError(f'{at}: section outside a case')
            section = _trim_ws(line[4:])
            if section in ('setup', 'source', 'expect'):
                cur[section] = []
            elif section != 'note':
                raise RuntimeError(f'{at}: unknown section {section}')
            continue
        if cur is None or section is None:
            continue                              # header text, ignored
        if section == 'note':
            continue
        cur[section].append(line)

    out = []
    for c in cases:
        if c['source'] is None:
            raise RuntimeError(f'{c["at"]}: case {c["name"]} has no --- source')
        if c['expect'] is None:
            raise RuntimeError(f'{c["at"]}: case {c["name"]} has no --- expect')
        c['setup'] = None if c['setup'] is None else _trim_ws('\n'.join(c['setup']))
        c['source'] = _trim_ws('\n'.join(c['source']))
        c['expect'] = _trim_ws('\n'.join(c['expect']))
        out.append(c)
    return out


# --- expectations -----------------------------------------------------------

def unescape(lit, at):
    if len(lit) < 2 or lit[0] != '"' or lit[-1] != '"':
        raise RuntimeError(f'{at}: expected a quoted string, got {lit}')
    body = lit[1:-1]
    out = []
    i = 0
    while i < len(body):
        if body[i] != '\\':
            out.append(body[i])
            i += 1
            continue
        i += 1
        e = body[i]
        if e == '\\':
            out.append('\\')
        elif e == '"':
            out.append('"')
        elif e == 'n':
            out.append('\n')
        elif e == 't':
            out.append('\t')
        elif e == 'r':
            out.append('\r')
        elif e == 'u':
            out.append(chr(int(body[i + 1:i + 5], 16)))
            i += 4
        else:
            raise RuntimeError(f'{at}: bad escape \\{e}')
        i += 1
    return ''.join(out)


def _json_quote(s):
    """The expectation vocabulary quotes text the way JSON.stringify does, so
    failure reports read the same as the other hosts'.
    """
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\r':
            out.append('\\r')
        elif ord(ch) < 0x20:
            out.append(f'\\u{ord(ch):04x}')
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def describe(value):
    if value.kind == 'TEXT':
        return f'tree {value.dump()}' if value.size() else f'text {_json_quote(value.scalar)}'
    if value.kind == 'BIN':
        return f'tree {value.dump()}' if value.size() else f'bin {value.dump()[1:]}'
    if value.kind == 'BOOL':
        return (f'tree {value.dump()}' if value.size()
                else f'bool {"TRUE" if value.scalar else "FALSE"}')
    return f'tree {value.dump()}' if value.size() else 'none'


def check(expect, value, error, at):
    space = expect.find(' ')
    form = expect if space < 0 else expect[:space]
    rest = '' if space < 0 else expect[space + 1:].strip()

    if form == 'error':
        if error is None:
            return f'expected {expect}, got value {describe(value)}'
        m = _ERR.match(rest)
        if not m:
            raise RuntimeError(f'{at}: malformed error expectation')
        if error.code != m.group(1):
            return f'expected {m.group(1)}, got {error.code} ({error.message})'
        if m.group(2) is not None:
            got_at = f'{error.line}:{error.col}'
            want_at = f'{m.group(2)}:{m.group(3)}'
            if got_at != want_at:
                return f'expected {m.group(1)} at {want_at}, got it at {got_at}'
        return None

    if error is not None:
        return f'expected {expect}, got {error.code} ({error.message})'

    if form == 'text':
        if value.kind != 'TEXT' or value.size():
            return f'wanted text, got {describe(value)}'
        return None if value.scalar == unescape(rest, at) else f'got {describe(value)}'
    if form == 'num':
        if value.kind != 'TEXT' or value.size():
            return f'wanted a number, got {describe(value)}'
        return None if value.scalar == rest else f'got {describe(value)}'
    if form == 'bin':
        if value.kind != 'BIN' or value.size():
            return f'wanted binary, got {describe(value)}'
        return None if value.dump()[1:] == rest else f'got {describe(value)}'
    if form == 'bool':
        if value.kind != 'BOOL' or value.size():
            return f'wanted a boolean, got {describe(value)}'
        got = 'TRUE' if value.scalar else 'FALSE'
        return None if got == rest else f'got {describe(value)}'
    if form == 'none':
        return None if (value.kind == 'NONE' and value.size() == 0) else f'got {describe(value)}'
    if form == 'tree':
        return None if value.dump() == rest else f'got tree {value.dump()}'
    raise RuntimeError(f'{at}: unknown expectation form {form}')


# --- running ----------------------------------------------------------------

def run_case(c):
    root = Value.none()
    if c['setup']:
        try:
            sel_compile(c['setup']).run(root)
        except SelError as e:
            # A broken setup is a suite bug, not a failing implementation.
            return {'suite_error': str(e)}
        except Exception as e:                       # noqa: BLE001
            return {'suite_error': str(e)}
    try:
        return {'value': sel_compile(c['source']).run(root)}
    except SelError as e:
        return {'error': e}
    except Exception as e:                           # noqa: BLE001
        # The same guard the setup phase above already has. Without it a host
        # exception escaped run_case and took the whole run with it, so this
        # host reported nothing at all rather than one failing case: a 4301-digit
        # literal raised ValueError out of CPython's int() and 599 other cases
        # went unreported. A host failing where the other five return a value is
        # exactly what the suite exists to show, so it has to survive being told.
        return {'suite_error': f'host error: {type(e).__name__}: {e}'}


def main():
    args = sys.argv[1:]
    if args:
        files = [os.path.abspath(f) for f in args]
    else:
        files = sorted(os.path.join(SUITE, f)
                       for f in os.listdir(SUITE) if f.endswith('.selt'))

    npass = 0
    failures = []
    suite_errors = []
    seen = {}

    for path in files:
        short = os.path.relpath(path, SUITE) if path.startswith(SUITE) else path
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        for c in parse_selt(text, short):
            if c['name'] in seen:
                suite_errors.append(
                    f'{c["at"]}: duplicate case name {c["name"]} (also {seen[c["name"]]})')
                continue
            seen[c['name']] = c['at']

            r = run_case(c)
            if 'suite_error' in r:
                suite_errors.append(f'{c["at"]}: {c["name"]}: setup failed: {r["suite_error"]}')
                continue
            problem = check(c['expect'], r.get('value'), r.get('error'), c['at'])
            if problem is None:
                npass += 1
            else:
                failures.append((c, problem))

    for c, problem in failures:
        indent = '\n             '
        print(f'FAIL {c["name"]}  ({c["at"]})')
        print(f'     source: {c["source"].replace(chr(10), indent)}')
        print(f'     want:   {c["expect"]}')
        print(f'     {problem}')
    for e in suite_errors:
        print(f'SUITE {e}')

    print(f'\n{npass} passed, {len(failures)} failed, {len(suite_errors)} suite errors')
    return 1 if (failures or suite_errors) else 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    sys.exit(main())
