"""SEL command line: evaluate an expression, a file, or start a REPL.

    sel -e 'EXPR'          evaluate and print
    sel file.sel           evaluate a file
    sel --deps -e 'EXPR'   print the variables the expression reads
    sel --functions        list the function table
    sel                    REPL, keeping one context across lines

Also reachable as `python -m sel`, which is the spelling to prefer when the
`sel` console script collides with another package's — the JS package installs
a `sel` command too.
"""

from __future__ import annotations

import sys

from . import SelError, Value, compile as sel_compile, function_names


def show(v: Value) -> str:
    if v.size() == 0:
        if v.kind == 'TEXT':
            return v.scalar
        if v.kind == 'BOOL':
            return 'TRUE' if v.scalar else 'FALSE'
        if v.kind == 'BIN':
            return f'bin:{v.dump()[1:]}'
    return v.dump()


def _report(e: SelError) -> None:
    sys.stderr.write(f'{e.code} at line {e.line} column {e.col}: {e.message}\n')


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    want_deps = '--deps' in argv
    args = [a for a in argv if a != '--deps']

    if args and args[0] == '--functions':
        print('\n'.join(function_names()))
        return 0

    source = None
    if args and args[0] == '-e':
        if len(args) < 2:
            sys.stderr.write('sel: -e needs an expression\n')
            return 2
        source = args[1]
    elif args:
        try:
            with open(args[0], encoding='utf-8') as fh:
                source = fh.read()
        except OSError as e:
            sys.stderr.write(f'sel: {e}\n')
            return 2

    if source is not None:
        try:
            program = sel_compile(source)
            if want_deps:
                print('\n'.join(program.dependencies()))
            else:
                print(show(program.run(Value.none())))
        except SelError as e:
            _report(e)
            return 1
        return 0

    # REPL: one context for the whole session, so assignments persist.
    root = Value.none()
    while True:
        try:
            line = input('sel> ')
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line.strip():
            continue
        try:
            print(show(sel_compile(line).run(root)))
        except SelError as e:
            _report(e)
    return 0
