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
DOCS = ['README.md', 'docs/LANGUAGE.md', 'docs/EXTENDING.md',
        'docs/SQL-TRANSLATION.md', 'docs/history/SQL-TESTING.md', 'PACKAGING.md']

FROM = re.compile(r'^<!--\s*from:\s*(\S+)\s*-->\s*$')


def region(path):
    """The lines strictly between EXAMPLE-BEGIN and EXAMPLE-END.

    The markers are comments in each file's own syntax, so match on the text
    rather than on a fixed prefix."""
    with open(os.path.join(ROOT, path), encoding='utf-8') as fh:
        lines = fh.read().split('\n')
    try:
        i = next(n for n, l in enumerate(lines) if 'EXAMPLE-BEGIN' in l)
        j = next(n for n, l in enumerate(lines) if 'EXAMPLE-END' in l)
    except StopIteration:
        return None
    return '\n'.join(lines[i + 1:j]).rstrip('\n')


def main():
    checked = 0
    bad = 0
    for doc in DOCS:
        full = os.path.join(ROOT, doc)
        if not os.path.exists(full):
            continue
        with open(full, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
        for n, line in enumerate(lines):
            m = FROM.match(line)
            if not m:
                continue
            path = m.group(1)
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
            want = region(path)
            if want is None:
                print('%s:%d: %s has no EXAMPLE-BEGIN/EXAMPLE-END region'
                      % (doc, n + 1, path), file=sys.stderr)
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
