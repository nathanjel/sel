#!/usr/bin/env python3
"""The message details every host agrees on (spec/errors.md, "Message conventions").

Messages are not normative and no conformance case reads one, but a command
line prints them, so a few details are agreed and this compares them across
every available host. It runs each conformance case that expects E_REGEX_SYNTAX,
E_SYNTAX or E_NOT_NUM (and has no setup) through each host's `sel` command and
requires, wherever a host's message carries one of them, that every host's
message carries the same:

  - the regex detail `(at offset N of /P/)` of an E_REGEX_SYNTAX;
  - the character of the lexer's `unexpected character "c" (U+XXXX)`;
  - the quoted text of `not a number: "..."`.

The rest of a message is free to differ.

    tools/check-messages.py            every available host (tools/impls.sh)
    SEL_IMPLS="js cpp" tools/check-messages.py
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CODES = ('E_REGEX_SYNTAX', 'E_SYNTAX', 'E_NOT_NUM')
DETAILS = (
    ('regex detail', re.compile(r'(\(at offset \d+ of /.*/\))$')),
    ('unexpected character', re.compile(r'unexpected character (.*)$')),
    ('not a number', re.compile(r'not a number: (.*)$')),
)


def impls():
    out = subprocess.run(['bash', '-c', '. tools/impls.sh; available_impls'], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.split()
    # The bundles are the js host's sources, bundled: the same messages.
    return [i for i in out if i not in ('js-bundle', 'js-bundle-min')]


def cases():
    """(name, source) of every setup-free conformance case expecting one of CODES."""
    found = []
    cdir = os.path.join(ROOT, 'conformance')
    for fname in sorted(os.listdir(cdir)):
        if not fname.endswith('.selt'):
            continue
        with open(os.path.join(cdir, fname), 'rb') as fh:
            text = fh.read().decode('utf-8')
        name, sections, cur = None, {}, None

        def flush():
            if name is None or 'setup' in sections or 'source' not in sections:
                return
            expect = '\n'.join(sections.get('expect', [])).strip(' \t\r\n')
            if any(expect == f'error {c}' or expect.startswith(f'error {c} ') for c in CODES):
                found.append((name, '\n'.join(sections['source']).strip(' \t\r\n')))
        for line in text.split('\n'):
            if line.startswith('### '):
                flush()
                m = re.match(r'### name: (\S+)', line)
                name, sections, cur = (m.group(1) if m else None), {}, None
            elif line.startswith('--- '):
                cur = line[4:].strip()
                sections[cur] = []
            elif line == '===':
                cur = None
            elif cur is not None:
                sections[cur].append(line)
        flush()
    return found


TIMEOUT = object()


def main():
    hosts = impls()
    if len(hosts) < 2:
        print(f'check-messages: need at least two hosts, have: {" ".join(hosts) or "none"}', file=sys.stderr)
        return 1
    todo = cases()
    if not todo:
        print('check-messages: no cases found', file=sys.stderr)
        return 1
    work = tempfile.mkdtemp(prefix='sel-messages-')
    for i, (_, src) in enumerate(todo):
        with open(os.path.join(work, f'{i}.sel'), 'wb') as fh:
            fh.write(src.encode('utf-8'))

    # Under the gate this runs inside `sel_hold php` (tools/check.sh): one PHP
    # slot is already held, so impl_cli's sel_php runs at once instead of waiting
    # for a slot inside the timeout below -- and the PHP probes then take turns
    # on that one slot here, which keeps PHP within the gate's bound.
    php_turn = threading.Semaphore(1) if 'php' in os.environ.get('SEL_SLOT_HELD', '').split() else None

    def run(job):
        i, host = job
        path = os.path.join(work, f'{i}.sel')
        held = php_turn if host == 'php' else None
        try:
            if held:
                held.acquire()
            r = subprocess.run(['bash', '-c', '. tools/impls.sh; impl_cli "$0" "$1"', host, path],
                               cwd=ROOT, capture_output=True, timeout=300)
            err = r.stderr.decode('utf-8', 'replace').split('\n')[0]
        except subprocess.TimeoutExpired:
            err = TIMEOUT
        finally:
            if held:
                held.release()
        return i, host, err

    jobs = int(os.environ.get('SEL_JOBS') or max(2, (os.cpu_count() or 4) // 2))
    messages = {}
    with ThreadPoolExecutor(jobs) as pool:
        for i, host, err in pool.map(run, [(i, h) for i in range(len(todo)) for h in hosts]):
            messages.setdefault(i, {})[host] = err

    failures = 0
    # A run that timed out says nothing about its message; report it as what it
    # is rather than as a disagreement over a detail it never printed.
    for i, (name, _) in enumerate(todo):
        for h, m in messages[i].items():
            if m is TIMEOUT:
                failures += 1
                print(f'TIMEOUT {name}: {h} gave no answer within 300 s')
        messages[i] = {h: m for h, m in messages[i].items() if m is not TIMEOUT}
    for i, (name, _) in enumerate(todo):
        for what, rx in DETAILS:
            got = {h: (rx.search(m).group(1) if rx.search(m) else None) for h, m in messages[i].items()}
            if all(v is None for v in got.values()) or len(set(got.values())) == 1:
                continue
            failures += 1
            print(f'DIFF {name}: {what}')
            groups = {}
            for h, v in got.items():
                groups.setdefault(v, []).append(h)
            for v, hs in groups.items():
                shown = '(none)' if v is None else v if len(v) <= 120 else v[:117] + '...'
                print(f'    {",".join(hs):<36} {shown}')
    if failures:
        print(f'check-messages: {failures} disagreement(s) or timeout(s) over {len(todo)} cases on: {" ".join(hosts)}')
        return 1
    print(f'check-messages: {len(todo)} cases, the agreed details agree on: {" ".join(hosts)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
