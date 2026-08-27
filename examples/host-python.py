#!/usr/bin/env python3
"""Calling SEL from Python. Run: python3 examples/host-python.py

Mirrors examples/host-js.mjs section for section, so the two can be read side by
side. The differences are Python's, not SEL's.
"""

import json
import os
import sys

try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'python'))

from sel import SelError, Value, compile, evaluate               # noqa: E402

# 1 - evaluate something -----------------------------------------------------

print('1. one-off')
print('  ', evaluate('2.50 + 2.50').as_text())

# 2 - compile once, run per keystroke ----------------------------------------
# Parsing is cheap but not free, and a Program is immutable and reusable. In a
# form you compile each rule once and keep it.

print('2. compile once, run many')
rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")')

for row in [{'QTY': '3', 'PRICE': '19.99'}, {'QTY': '1', 'PRICE': '5.00'}]:
    ctx = Value.from_native({**row, 'LIMIT': '50.00'})
    print('  ', rule.run(ctx).as_text())

# 3 - building a context ------------------------------------------------------
# from_native takes scalars, lists and nested dicts. Pass money as *strings*: a
# Python float is a double and has already lost the exactness SEL preserves.
# from_native refuses a float outright rather than guess a decimal form for it,
# so this is enforced at the boundary and not left to discipline.

print('3. structured context')
order = Value.from_native({
    'CUSTOMER': 'Zażółć',
    'ITEMS': [                                    # a list is a 1-based SEL list
        {'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'},
        {'SKU': 'CD-5678', 'QTY': '1', 'PRICE': '5.01'},
    ],
})
print('   first SKU:', compile('ITEMS[1]["SKU"]').run(order).as_text())
print('   total:    ', compile('SUM(ITEMS, _["QTY"] * _["PRICE"])').run(order).as_text())
print('   0.1+0.2:  ', evaluate('0.10 + 0.20').as_text(), '  (Python says', 0.1 + 0.2, ')')

try:
    Value.from_native({'PRICE': 19.99})
except TypeError as e:
    print('   floats:   ', e)

# 4 - reading results back ----------------------------------------------------
# A result is a Value. Use as_text/as_bool/as_decimal for scalars, or walk it.

print('4. reading results')
v = evaluate('SPLIT("a,b,c", ",")')
print('   count:  ', v.size())
print('   keys:   ', ','.join(v.keys()))
print('   [2]:    ', v.get('2').as_text())
print('   scalar: ', v.as_text())                 # scalar context: first child
print('   native: ', json.dumps(v.to_native()))
print('   bool:   ', evaluate('1 < 2').as_bool())
# Value also supports len(), `in` and [] for the Python-shaped spelling. These
# are conveniences on top of the cross-host API, not a replacement for it.
print('   len/[]: ', len(v), v['2'].as_text(), '2' in v)

# 5 - the context is mutated, so rules can hand values back -------------------

print('5. reading variables the rule set')
ctx = Value.from_native({'QTY': '3', 'PRICE': '19.99'})
compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT').run(ctx)
for name in ['NET', 'VAT', 'GROSS']:
    print(f'   {name} =', ctx.get(name).as_text())

# 6 - errors ------------------------------------------------------------------
# Everything raises SelError with a stable code and the position of the node
# that actually failed. Assert on .code, never on the message.

print('6. errors')
for src in ['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")']:
    try:
        evaluate(src)
    except SelError as e:
        print(f'   {src:<18} {e.code} at {e.line}:{e.col} — {e.message}')

# 7 - which fields does this rule read? ---------------------------------------
# Static, without running it. Wire these to your input listeners and you have
# re-validation for free.

print('7. dependencies')
p = compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""')
print('  ', ' '.join(p.dependencies()))
