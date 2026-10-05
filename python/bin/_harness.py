"""What the Python runners in this directory share: where `sel` is imported
from, how a file is read, and how a corpus is cut into records. Imported by its bare name (a script's own
directory is first on sys.path), so it works the same under the source tree and
under the wheel's interpreter.

The rules are tools/README.md's, and every host's runners follow them:

- A file is read as bytes and decoded as UTF-8 with NO newline translation.
  Python's text layer (``open(path, encoding=...)``, ``Path.read_text``) uses
  universal newlines and turns CRLF and a lone CR into LF before the reader sees
  them, so a case whose program holds a CR was a different program here than in
  every other host (``lex.space.crlf-between-tokens``, ``lex.space.lone-cr``).
- A path that cannot be read -- missing, unreadable, a directory -- ends the
  run with one line, ``cannot read <path>: <reason>``, on stderr and a non-zero
  status; never a traceback.
- A run that executed no case at all -- an empty file, a filter that matched
  nothing -- is a failure, not a green ``0 passed``.
"""

from __future__ import annotations

import os
import sys

# Every runner imports this module before `sel`. An *installed* sel is preferred
# over the source tree, so the python-wheel implementation in tools/impls.sh
# grades the built package rather than silently re-testing python/sel through a
# path insert; the source tree is the fallback when nothing is installed, which
# is how the plain `python` implementation and a bare checkout run. (Index 1:
# the script's own directory stays first, for the generated case data.)
try:
    import sel  # noqa: F401
except ImportError:
    sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))


def read_text(path: str) -> str:
    """The file's bytes decoded as UTF-8, CR bytes and all. Exits the process
    with status 1 and ``cannot read <path>`` when the path cannot be read."""
    try:
        with open(path, 'rb') as fh:
            data = fh.read()
    except OSError as e:
        sys.stderr.write(f'cannot read {path}: {e.strerror or e}\n')
        sys.exit(1)
    return data.decode('utf-8')


def usage(text: str) -> None:
    """A runner called without what it needs: one line on stderr, status 2."""
    sys.stderr.write(f'usage: {text}\n')
    sys.exit(2)


def none_ran(message: str) -> int:
    """Report a run that executed nothing; returns the exit status, 1."""
    sys.stderr.write(f'{message}\n')
    return 1


def read_corpus(text: str) -> list[str]:
    """A line beginning ``### `` starts a record; everything after it is source
    until the next marker.

    tools/README.md makes one sentence of this normative: *the record is the
    joined lines with exactly one trailing newline removed* -- one ``\\n``, never
    a ``\\r`` (a CR anywhere, before an LF included, is program text) and never
    a second ``\\n``. Earlier readers got this wrong and produced phantom
    disagreements that looked like interpreter bugs.

    Two Python-specific ways to get it wrong, both avoided here: ``.splitlines()``
    would also break records on \\v, \\f, \\x1c-\\x1e, U+0085, U+2028 and U+2029
    (and on a lone CR), and ``.rstrip('\\n')`` would strip *all* trailing
    newlines rather than one.
    """
    records: list[list[str]] = []
    cur: list[str] | None = None
    for line in text.split('\n'):
        if line.startswith('### '):
            cur = []
            records.append(cur)
            continue
        if cur is not None:
            cur.append(line)
    out = []
    for lines in records:
        joined = '\n'.join(lines)
        out.append(joined[:-1] if joined.endswith('\n') else joined)
    return out
