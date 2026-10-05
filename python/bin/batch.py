#!/usr/bin/env python3
"""Runs a corpus of SEL programs and prints one canonical line each, so every
implementation's output can be compared with a plain diff. Both the corpus
format and the line format are specified in tools/README.md.

    python3 python/bin/batch.py [--show] corpus.selc
"""

import os
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

from _harness import read_corpus, read_text                # noqa: E402


def render(v):
    """The rendering bin/sel uses, so a documentation example can be pasted into
    the CLI and produce exactly what the documentation claims.
    """
    if v.size() == 0:
        if v.kind == 'TEXT':
            return v.scalar
        if v.kind == 'BOOL':
            return 'TRUE' if v.scalar else 'FALSE'
        if v.kind == 'BIN':
            return f'bin:{v.dump()[1:]}'
    return v.dump()


def main():
    args = sys.argv[1:]
    show = '--show' in args
    path = [a for a in args if a != '--show'][0]

    text = read_text(path)

    lines = []
    for src in read_corpus(text):
        try:
            v = sel_compile(src).run(Value.none())
            lines.append(render(v) if show else v.dump())
        except SelError as e:
            lines.append(f'!{e.code}' if show else f'!{e.code}@{e.line}:{e.col}')
        except RecursionError:
            # Never converted to E_DEPTH: the position would be a guess, and it
            # would hide the bug. A !HOST line is always a bug (tools/README.md).
            lines.append('!HOST RecursionError: maximum recursion depth exceeded')
        except Exception as e:                                  # noqa: BLE001
            lines.append(f'!HOST {type(e).__name__}: {e}')
    # One line per program is the protocol; a value containing a newline must not
    # be allowed to desynchronise the comparison.
    sys.stdout.write('\n'.join(ln.replace('\n', '\\n') for ln in lines) + '\n')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', newline='\n')
    sys.exit(main())
