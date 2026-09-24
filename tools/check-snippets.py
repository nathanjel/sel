#!/usr/bin/env python3
"""Checks that every code block the documentation quotes from examples/ still
matches the file it was taken from.

    tools/check-snippets.py

WHY THIS EXISTS. The worked examples used to live inline in docs/, where nothing
could run them: tools/check-docs.sh verifies only the `EXPRESSION => RESULT`
lines inside ```sel blocks, so every host-language snippet was unchecked prose.
Moving them into examples/ is what lets tools/check-examples.sh run them -- but
a documentation that then *retypes* the code has simply moved the drift rather
than removed it.

So the docs do not retype it. A block is marked with the file it came from:

    <!-- from: examples/fn-simple/js.mjs -->
    ```js
    ...
    ```

and this asserts the block is byte-identical to that file's EXAMPLE-BEGIN /
EXAMPLE-END region. Edit the example, and the doc is wrong until it is updated;
edit the doc, and it is wrong until the example agrees. Neither can drift
quietly, which is the only property that makes "snippets and links" safe.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def docs():
    """README.md, PACKAGING.md and every Markdown file under docs/, so a new
    page is checked without being registered here."""
    out = ['README.md', 'PACKAGING.md']
    for base, _, files in sorted(os.walk(os.path.join(ROOT, 'docs'))):
        out += sorted(os.path.relpath(os.path.join(base, f), ROOT)
                      for f in files if f.endswith('.md'))
    return out

FROM = re.compile(r'^<!--\s*from:\s*(\S+)\s*-->\s*$')


MARK = re.compile(r'EXAMPLE-(BEGIN|END)(?:\s+([\w-]+))?')


def region(path, name=None):
    """The lines strictly between EXAMPLE-BEGIN and EXAMPLE-END.

    The markers are comments in each file's own syntax, so match on the text
    rather than on a fixed prefix. A file may hold several regions, each named
    after the marker (`EXAMPLE-BEGIN rules`), and a doc quotes one of them as
    `path#rules`; an unnamed quote takes the file's first region."""
    with open(os.path.join(ROOT, path), encoding='utf-8') as fh:
        lines = fh.read().split('\n')
    if name is None and not any(MARK.search(l) for l in lines):
        # A file with no regions at all -- an example's recorded output.txt --
        # is quoted whole.
        return '\n'.join(lines).rstrip('\n')
    start = None
    for n, line in enumerate(lines):
        m = MARK.search(line)
        if not m:
            continue
        if m.group(1) == 'BEGIN' and start is None and (name is None or m.group(2) == name):
            start = n
        elif m.group(1) == 'END' and start is not None and m.group(2) == lines_name(lines[start]):
            return '\n'.join(dedent(lines[start + 1:n])).rstrip('\n')
    return None


def lines_name(line):
    return MARK.search(line).group(2)


def dedent(lines):
    """A region inside a function or a block is quoted without the indent it
    has there: the doc shows the code, not where it sits."""
    indents = [len(l) - len(l.lstrip(' ')) for l in lines if l.strip()]
    cut = min(indents) if indents else 0
    return [l[cut:] for l in lines]


def main():
    checked = 0
    bad = 0
    for doc in docs():
        full = os.path.join(ROOT, doc)
        if not os.path.exists(full):
            continue
        with open(full, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
        for n, line in enumerate(lines):
            m = FROM.match(line)
            if not m:
                continue
            path, _, name = m.group(1).partition('#')
            # The fence must open on the next line: anything else means the
            # marker has drifted away from the block it labels, which would
            # otherwise silently check nothing.
            if n + 1 >= len(lines) or not lines[n + 1].startswith('```'):
                print('%s:%d: `from:` marker is not followed by a code fence'
                      % (doc, n + 1), file=sys.stderr)
                bad += 1
                continue
            end = next((k for k in range(n + 2, len(lines))
                        if lines[k].startswith('```')), None)
            if end is None:
                print('%s:%d: unterminated code fence' % (doc, n + 1),
                      file=sys.stderr)
                bad += 1
                continue
            quoted = '\n'.join(lines[n + 2:end]).rstrip('\n')

            if not os.path.exists(os.path.join(ROOT, path)):
                print('%s:%d: quotes %s, which does not exist'
                      % (doc, n + 1, path), file=sys.stderr)
                bad += 1
                continue
            want = region(path, name or None)
            if want is None:
                print('%s:%d: %s has no EXAMPLE-BEGIN/EXAMPLE-END region%s'
                      % (doc, n + 1, path, ' named ' + name if name else ''), file=sys.stderr)
                bad += 1
                continue
            checked += 1
            if quoted != want:
                print('%s:%d: the block does not match %s'
                      % (doc, n + 1, path), file=sys.stderr)
                for d in _diff(want, quoted):
                    print('    ' + d, file=sys.stderr)
                bad += 1

    if bad:
        print('\nDOCUMENTATION QUOTES ARE STALE — %d block(s); the example is the '
              'original, the doc is the copy' % bad, file=sys.stderr)
        return 1
    # Zero is not success: it means every marker vanished, which is how this
    # check would pass while watching nothing at all.
    if checked == 0:
        print('snippets: no `from:` markers found in any documentation file',
              file=sys.stderr)
        return 1
    print('snippets: %d documentation block(s) match their example' % checked)
    return 0


def _diff(want, got):
    import difflib
    return list(difflib.unified_diff(want.split('\n'), got.split('\n'),
                                     'examples', 'docs', lineterm=''))[:12]


if __name__ == '__main__':
    sys.exit(main())
