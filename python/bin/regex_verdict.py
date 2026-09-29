#!/usr/bin/env python3
"""Verdict driver for tools/check-regex-ambiguity-diff.py: reads one pattern per
line on stdin, prints `A` when this host's validator accepts it and `R` when it
refuses it with E_REGEX_SYNTAX. An optional argument `i` checks under the i flag."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from sel import SelError                      # noqa: E402
from sel.builtins import regex                # noqa: E402

ic = len(sys.argv) > 1 and sys.argv[1] == 'i'
for line in sys.stdin.read().split('\n')[:-1]:
    try:
        regex.validate(line, None, ic)
        print('A')
    except SelError as e:
        print('R' if e.code == 'E_REGEX_SYNTAX' else 'E:' + e.code)
