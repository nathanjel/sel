#!/usr/bin/env python3
"""Drives examples/order-validation.sel through the Python host API and prints a
canonical report. examples/e2e.mjs does the same through the JS host API, and
tools/e2e.sh diffs them.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Prefer an *installed* sel over the source tree, so the python-wheel
# implementation in tools/impls.sh actually exercises the built package rather
# than silently re-testing python/sel through a path insert. Falls back to the
# source tree when nothing is installed, which is how the plain `python`
# implementation and a bare checkout run.
try:
    import sel as _sel_probe                                    # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, '..', 'python'))

from sel import SelError, Value, compile as sel_compile   # noqa: E402

with open(os.path.join(HERE, 'order-validation.sel'), encoding='utf-8') as fh:
    source = fh.read()
program = sel_compile(source)

# Prices are strings, not Python floats: 0.1 + 0.2 must not become
# 0.30000000000000004 on one side of the wire and 0.30 on the other. The host
# boundary refuses a float outright, so this is enforced rather than remembered.
SCENARIOS = {
    'valid order': {
        'CUSTOMER': 'Zażółć Gęślą',
        'POSTCODE': '31-874',
        'CREDIT_LIMIT': '1000.00',
        'ITEMS': [
            {'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'},
            {'SKU': 'CD-5678', 'QTY': '1', 'PRICE': '5.01'},
        ],
    },
    'blank customer': {
        'CUSTOMER': '   ', 'POSTCODE': '31-874', 'CREDIT_LIMIT': '1000.00',
        'ITEMS': [{'SKU': 'AB-1234', 'QTY': '1', 'PRICE': '1.00'}],
    },
    'bad postcode': {
        'CUSTOMER': 'Anna', 'POSTCODE': '318744', 'CREDIT_LIMIT': '1000.00',
        'ITEMS': [{'SKU': 'AB-1234', 'QTY': '1', 'PRICE': '1.00'}],
    },
    'no lines': {
        'CUSTOMER': 'Anna', 'POSTCODE': '31-874', 'CREDIT_LIMIT': '1000.00',
        'ITEMS': [],
    },
    'zero quantity': {
        'CUSTOMER': 'Anna', 'POSTCODE': '31-874', 'CREDIT_LIMIT': '1000.00',
        'ITEMS': [
            {'SKU': 'AB-1234', 'QTY': '1', 'PRICE': '1.00'},
            {'SKU': 'CD-5678', 'QTY': '0', 'PRICE': '2.00'},
        ],
    },
    'malformed sku': {
        'CUSTOMER': 'Anna', 'POSTCODE': '31-874', 'CREDIT_LIMIT': '1000.00',
        'ITEMS': [{'SKU': 'oops', 'QTY': '1', 'PRICE': '1.00'}],
    },
    'over credit limit': {
        'CUSTOMER': 'Anna', 'POSTCODE': '31-874', 'CREDIT_LIMIT': '10.00',
        'ITEMS': [{'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'}],
    },
    'exact-cent arithmetic': {
        'CUSTOMER': 'Anna', 'POSTCODE': '31-874', 'CREDIT_LIMIT': '0.30',
        'ITEMS': [
            {'SKU': 'AB-1234', 'QTY': '1', 'PRICE': '0.10'},
            {'SKU': 'CD-5678', 'QTY': '1', 'PRICE': '0.20'},
        ],
    },
}

lines = [f'dependencies: {" ".join(program.dependencies())}']
for name, data in SCENARIOS.items():
    try:
        result = program.run(Value.from_native(data)).dump()
    except SelError as e:
        result = f'!{e.code}@{e.line}:{e.col}'
    except Exception as e:                                   # noqa: BLE001
        result = f'!HOST {e}'
    lines.append(f'{name.ljust(24)} {result}')

sys.stdout.reconfigure(encoding='utf-8', newline='\n')
sys.stdout.write('\n'.join(lines) + '\n')
