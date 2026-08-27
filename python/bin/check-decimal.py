#!/usr/bin/env python3
"""Checks the Python decimal core against the Python oracle.

    python3 python/bin/check-decimal.py oracle.txt

Both sides are Python, which looks circular and is not: the oracle is the
stdlib `decimal` module and sel.decimal shares no code with it — see the module
docstring in sel/decimal.py for why that separation is load-bearing.
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

from sel import decimal as D          # noqa: E402
from sel.errors import SelError       # noqa: E402


def main() -> int:
    with open(sys.argv[1], encoding='utf-8') as fh:
        # .split('\n'), never .splitlines(): the latter also splits on \v, \f,
        # \x1c-\x1e, U+0085, U+2028 and U+2029, none of which end a record here.
        lines = [ln for ln in fh.read().split('\n') if ln != '']

    failures = []
    # Counted separately from the displayed list: capping both would report "20
    # mismatches" whether 20 or 20000 cases were wrong, which is exactly the
    # moment the number matters.
    mismatches = 0

    for line in lines:
        op, a_s, b_s, want = line.split('|')
        a = D.parse(a_s)
        b = D.parse(b_s)
        try:
            if op == '+':
                got = D.format(D.add(a, b))
            elif op == '-':
                got = D.format(D.sub(a, b))
            elif op == '*':
                got = D.format(D.mul(a, b))
            elif op == '/':
                got = D.format(D.div(a, b))
            elif op == '%':
                got = D.format(D.mod(a, b))
            elif op == 'cmp':
                got = str(D.cmp(a, b))
            elif op == 'round':
                got = D.format(D.round(a, int(b_s)))
            elif op == 'floor':
                got = D.format(D.floor(a))
            elif op == 'ceil':
                got = D.format(D.ceil(a))
            elif op == 'trunc':
                got = D.format(D.trunc(a))
            else:
                raise ValueError(f'unknown op {op}')
        except SelError as e:
            got = f'THREW {e.code}'
        except Exception as e:                     # noqa: BLE001
            got = f'THREW {e}'
        if got != want:
            mismatches += 1
            if len(failures) < 20:
                failures.append(f'{a_s} {op} {b_s} => {got}, oracle says {want}')

    print(f'python: {len(lines)} cases, {mismatches} mismatches')
    for f in failures:
        print(f'  {f}')
    if mismatches > len(failures):
        print(f'  ... and {mismatches - len(failures)} more')
    return 1 if mismatches else 0


if __name__ == '__main__':
    sys.exit(main())
