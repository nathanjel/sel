#!/usr/bin/env python3
"""Complex usage — the language's reach, from Python.

    python3 examples/complex/python.py

examples/plain/ is the API. This is the language: aggregates, named binders,
text and regex, structured results, and a rule that refuses. The four files
beside this one print byte-identical output; tools/check-examples.sh diffs
them.
"""

import os
import sys

try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'python'))

from sel import SelError, Value, compile, evaluate               # noqa: E402

order = Value.from_native({
    'CUSTOMER': 'Zażółć Gęślą',
    'POSTCODE': '31-874',
    'CREDIT_LIMIT': '100.00',
    'ITEMS': [
        {'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'},
        {'SKU': 'CD-5678', 'QTY': '1', 'PRICE': '5.01'},
        {'SKU': 'EF-9012', 'QTY': '2', 'PRICE': '0.50'},
    ],
})


def ask(src):
    return compile(src).run(order).as_text()


# 1 - a rule set, not an expression --------------------------------------------
# `;` separates statements and the last one is the answer. Intermediate names
# are ordinary variables, so a long rule reads top to bottom.

print('1. a rule set')
print('  ', compile('; '.join([
    'NET   = SUM(ITEMS, _["QTY"] * _["PRICE"])',
    'VAT   = ROUND(NET * 0.23, 2)',
    'GROSS = NET + VAT',
    'IF(GROSS > CREDIT_LIMIT, "refer: " & GROSS, "accept: " & GROSS)',
])).run(order).as_text())

# 2 - aggregates ----------------------------------------------------------------
# No loops. A body expression is evaluated once per element with `_` bound to
# the element and `_K` to its key.

print('2. aggregates')
print('   lines        =>', ask('COUNT(ITEMS)'))
print('   net          =>', ask('SUM(ITEMS, _["QTY"] * _["PRICE"])'))
print('   all in stock =>', ask('IF(ALL(ITEMS, _["QTY"] > 0), "TRUE", "FALSE")'))
print('   any > 10     =>', ask('IF(ANY(ITEMS, _["PRICE"] > 10.00), "TRUE", "FALSE")'))
print('   dearest      =>', ask('MAX(MAP(ITEMS, _["PRICE"]))'))

# 3 - MAP renumbers, FILTER keeps the keys ---------------------------------------
# A filtered list stays addressable the way its source was, which is why the
# dump below has holes in it. That is the contract, not an accident.

print('3. map and filter')
print('   skus         =>', ask('JOIN(MAP(ITEMS, _["SKU"]), ", ")'))
print('   bulk keys    =>', ','.join(compile('FILTER(ITEMS, _["QTY"] > 1)').run(order).keys()))
print('   keyed        =>', compile(
    'MAP(FILTER(ITEMS, _["QTY"] > 1), _K & ":" & _["SKU"])').run(order).dump())

# 4 - naming the binder, for nesting ----------------------------------------------
# `_` is the innermost element. The three-argument form names it instead, which
# is the only way an outer element stays reachable from an inner body.

print('4. named binders')
print('  ', evaluate(
    'R[1] = (1, 2); R[2] = (3, 4); IF(ALL(R, ROW, ALL(ROW, _ > 0)), "all positive", "no")'
).as_text())

# 5 - text and regex ----------------------------------------------------------------
# Patterns are a portable subset, checked at compile time: a regex that would
# mean different things on different hosts is refused rather than guessed at.

print('5. text and regex')
# UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
# ę below come back unchanged. That is not a shortcoming, it is the only way
# five hosts can agree. Measured on a sharp s: JS's toUpperCase and Python's
# str.upper both answer SS, PHP's strtoupper answers ß, and C's toupper cannot
# see it at all. SEL answers ß on all five, because it never asks the host.
print('   upper        =>', ask('UPPER(CUSTOMER)'))
print('   initials     =>', ask('JOIN(MAP(SPLIT(CUSTOMER, " "), LEFT(_, 1)), ".")'))
print('   postcode     =>', ask('IF(RMATCH(\'^[0-9]{2}-[0-9]{3}$\', POSTCODE), "ok", "bad")'))
# RGROUPS puts the WHOLE match at "1", so the first capture is "2".
print('   area         =>', ask('RGROUPS(\'^([0-9]{2})-\', POSTCODE)["2"]'))
print('   padded       =>', ask('PADL(COUNT(ITEMS), 3, "0")'))

# 6 - asking whether a key is there ---------------------------------------------------

print('6. presence')
print('   HAS SKU      =>', ask('IF(HAS(ITEMS[1], "SKU"), "TRUE", "FALSE")'))
print('   HAS NOTE     =>', ask('IF(HAS(ITEMS[1], "NOTE"), "TRUE", "FALSE")'))
try:
    missing = ask('ITEMS[1]["NOTE"]')
except SelError as e:
    missing = f'{e.code} at {e.line}:{e.col}'
print('   missing      =>', missing)

# 7 - a rule that refuses --------------------------------------------------------------
# ABORT is how a rule says "this is not valid", as distinct from "this could not
# be computed". Both arrive as the same error type, told apart by the code.

print('7. business refusal')
for label, src in [
    ('under limit',
     'IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > CREDIT_LIMIT, ABORT("over credit limit"), "ok")'),
    ('over limit',
     'IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > 50.00, ABORT("over credit limit"), "ok")'),
    ('blank name', 'IF(TRIM("   ") $== "", ABORT("customer required"), "ok")'),
]:
    try:
        print(f'   {label:<11} =>', ask(src))
    except SelError as e:
        print(f'   {label:<11} => {e.code}: {e.message}')

# 8 - where a failure actually happened --------------------------------------------------
# The position is the node that failed, not the statement or the call that
# contains it. That is what makes a long rule debuggable.

print('8. error positions')
for src in ['1 + ROUND(2 + "x", 2)', 'SUM(ITEMS, _["QTY"] * _["NOPE"])']:
    try:
        compile(src).run(order)
    except SelError as e:
        print(f'   {e.code} at {e.line}:{e.col}  {src}')
