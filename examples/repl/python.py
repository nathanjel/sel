#!/usr/bin/env python3
"""A read-eval-print loop -- the whole of it, in Python.

    PYTHONPATH=python python3 examples/repl/python.py
    PYTHONPATH=python python3 examples/repl/python.py < examples/repl/session.txt

One context lives across lines, so a variable assigned on one line is there
on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
expression reads, and `:reset` empties the context. Errors print their code
and position -- the message is human text and may differ between hosts; the
code and the position may not.

The four files beside this one print byte-identical output for the session in
session.txt.
"""

import os
import sys

try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'python'))

from sel import SelError, Value, compile                       # noqa: E402


# EXAMPLE-BEGIN repl
def show(value):
    if value.is_bool():
        return 'TRUE' if value.as_bool() else 'FALSE'
    if value.is_null():
        return 'NULL'
    if value.size() > 0 or value.is_bin():
        return value.dump()
    return value.as_text()


context = Value.none()
for line in sys.stdin:
    line = line.rstrip('\n')
    if line.strip() == '':
        continue
    print('sel>', line)
    try:
        if line == ':reset':
            context = Value.none()
        elif line.startswith(':deps '):
            print(' '.join(compile(line[6:]).dependencies()))
        else:
            print(show(compile(line).run(context)))
    except SelError as e:
        print(f'{e.code} at {e.line}:{e.col}')
# EXAMPLE-END repl
