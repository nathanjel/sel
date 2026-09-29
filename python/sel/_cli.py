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
from .utf8 import decode_source


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


def _read_line(prompt: str) -> str:
    """One REPL line, read as bytes and decoded strictly (SPEC 2). A terminal gets
    `input()`'s line editing, and its text has already been decoded by the
    interpreter, so only a lone surrogate can be left for the lexer to reject."""
    if sys.stdin.isatty():
        return input(prompt)
    sys.stdout.write(prompt)
    sys.stdout.flush()
    raw = sys.stdin.buffer.readline()
    if not raw:
        raise EOFError
    return decode_source(raw.rstrip(b'\n'))


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
        # Bytes, decoded by the project's strict codec: the native text layer
        # would translate CRLF and CR to LF (changing the program) and raise a
        # UnicodeDecodeError instead of E_UTF8 (SPEC 2).
        try:
            with open(args[0], 'rb') as fh:
                data = fh.read()
        except OSError as e:
            sys.stderr.write(f'sel: {e}\n')
            return 2
        try:
            source = decode_source(data)
        except SelError as e:
            _report(e)
            return 1

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
            line = _read_line('sel> ')
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        except SelError as e:
            _report(e)
            continue
        if not line.strip():
            continue
        try:
            print(show(sel_compile(line).run(root)))
        except SelError as e:
            _report(e)
