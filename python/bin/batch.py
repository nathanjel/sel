#!/usr/bin/env python3
"""Runs a corpus of SEL programs and prints one canonical line each, so every
implementation's output can be compared with a plain diff. Both the corpus
format and the line format are specified in tools/README.md.

    python3 python/bin/batch.py [--show] corpus.selc
"""

import sys

from _harness import none_ran, read_corpus, read_text, usage  # first: it makes `sel` importable

from sel import SelError, Value, compile as sel_compile   # noqa: E402
# --show renders as bin/sel does, so a documentation example can be pasted into
# the CLI and produce exactly what the documentation claims.
from sel._cli import show as render                        # noqa: E402


def main():
    args = sys.argv[1:]
    show = '--show' in args
    paths = [a for a in args if a != '--show']
    if len(paths) != 1:
        usage('batch.py [--show] corpus.selc')
    corpus = read_corpus(read_text(paths[0]))
    if not corpus:
        return none_ran(f'no programs ran: {paths[0]} holds no records')

    lines = []
    for src in corpus:
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
