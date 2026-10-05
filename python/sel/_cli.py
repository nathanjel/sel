"""SEL command line: evaluate an expression, a file, or start a REPL.

    sel -e 'EXPR'          evaluate and print
    sel file.sel           evaluate a file
    sel --deps -e 'EXPR'   print the variables the expression reads, one per line
    sel --functions        list the function table
    sel --help | --version
    sel                    REPL, keeping one context across lines

The contract every host's CLI follows is in docs/usage/repl.md: usage errors
exit 2 with `sel: ...` on stderr, an unreadable file exits 1 with `sel: cannot
read <path>`, an evaluation error exits 1, and the REPL writes its prompt only
to a terminal.

Also reachable as `python -m sel`, which is the spelling to prefer when the
`sel` console script collides with another package's — the JS package installs
a `sel` command too.
"""

from __future__ import annotations

import sys

from . import SelError, Value, __version__, compile as sel_compile, function_names
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


USAGE = """\
usage: sel [--deps] -e EXPR     evaluate EXPR and print the result
       sel [--deps] FILE        evaluate the program in FILE
       sel                      read one program per line from stdin (REPL)
       sel --functions          list the function table
       sel --help | --version

  --deps   print the variables the program reads, one per line, instead of
           running it
"""


def _usage_error(message: str) -> int:
    sys.stderr.write(f'sel: {message}\n')
    return 2


def _is_blank(line: str) -> bool:
    """SEL's whitespace and nothing else (space, TAB, CR, LF): ``str.strip()``
    would also skip a line of NBSP, VT or U+3000, which the lexer refuses."""
    return not line.strip(' \t\r\n')


def _read_line(tty: bool) -> str:
    """One REPL line, read as bytes and decoded strictly (SPEC 2). A terminal gets
    `input()`'s line editing and the prompt, and its text has already been
    decoded by the interpreter, so only a lone surrogate can be left for the
    lexer to reject. A pipe gets no prompt: nothing but results and errors."""
    if tty:
        return input('sel> ')
    raw = sys.stdin.buffer.readline()
    if not raw:
        raise EOFError
    return decode_source(raw[:-1] if raw.endswith(b'\n') else raw)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    want_deps = False
    source = None
    path = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg in ('-h', '--help'):
            sys.stdout.write(USAGE)
            return 0
        if arg == '--version':
            print(f'sel {__version__}')
            return 0
        if arg == '--functions':
            print('\n'.join(function_names()))
            return 0
        if arg == '--deps':
            want_deps = True
        elif arg == '-e':
            if i >= len(argv):
                return _usage_error('-e needs an expression')
            if source is not None or path is not None:
                return _usage_error(f'unexpected argument {argv[i]}')
            source = argv[i]
            i += 1
        elif len(arg) > 1 and arg.startswith('-'):
            return _usage_error(f'unknown option {arg}')
        elif source is not None or path is not None:
            return _usage_error(f'unexpected argument {arg}')
        else:
            path = arg

    if path is not None:
        # Bytes, decoded by the project's strict codec: the native text layer
        # would translate CRLF and CR to LF (changing the program) and raise a
        # UnicodeDecodeError instead of E_UTF8 (SPEC 2).
        try:
            with open(path, 'rb') as fh:
                data = fh.read()
        except OSError as e:
            sys.stderr.write(f'sel: cannot read {path}: {e.strerror or e}\n')
            return 1
        try:
            source = decode_source(data)
        except SelError as e:
            _report(e)
            return 1

    if source is not None:
        try:
            program = sel_compile(source)
            if want_deps:
                # One name per line; an empty list prints nothing at all.
                for name in program.dependencies():
                    print(name)
            else:
                print(show(program.run(Value.none())))
        except SelError as e:
            _report(e)
            return 1
        return 0

    # REPL: one context for the whole session, so assignments persist.
    tty = sys.stdin.isatty()
    root = Value.none()
    while True:
        try:
            line = _read_line(tty)
        except (EOFError, KeyboardInterrupt):
            if tty:
                print()
            return 0
        except SelError as e:
            _report(e)
            continue
        if _is_blank(line):
            continue
        try:
            print(show(sel_compile(line).run(root)))
        except SelError as e:
            _report(e)
