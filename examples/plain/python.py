#!/usr/bin/env python3
"""Plain usage — calling SEL from Python.

    python3 examples/plain/python.py

The four files beside this one do the same thing through their own host API and
print byte-identical output; tools/check-examples.sh diffs them. That is the
point of the example as much as the code is: the differences you see between
these files are the languages', never SEL's.
"""

import os
import sys

try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'python'))

from sel import SelError, Value, compile, evaluate               # noqa: E402

# 1 - evaluate something -------------------------------------------------------

print('1. one-off')
print('   2.50 + 2.50 =>', evaluate('2.50 + 2.50').as_text())

# 2 - compile once, run per keystroke ------------------------------------------
# Parsing is cheap but not free, and a Program is immutable and reusable. In a
# form you compile each rule once and keep it.

print('2. compile once, run many')
# EXAMPLE-BEGIN
rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")')
for row in [{'QTY': '3', 'PRICE': '19.99'}, {'QTY': '1', 'PRICE': '5.00'}]:
    ctx = Value.from_native({**row, 'LIMIT': '50.00'})
    print(f"   QTY={row['QTY']} PRICE={row['PRICE']} =>", rule.run(ctx).as_text())
# EXAMPLE-END

# 3 - building a context --------------------------------------------------------
# Pass money as *strings*. A Python float has already lost the exactness SEL
# exists to preserve, and from_native refuses one rather than pretend otherwise.

print('3. structured context')
order = Value.from_native({
    'CUSTOMER': 'Zażółć',
    'ITEMS': [                                    # a list is a 1-based SEL list
        {'SKU': 'AB-1234', 'QTY': '3', 'PRICE': '19.99'},
        {'SKU': 'CD-5678', 'QTY': '1', 'PRICE': '5.01'},
    ],
})
print('   first SKU =>', compile('ITEMS[1]["SKU"]').run(order).as_text())
print('   total     =>', compile('SUM(ITEMS, _["QTY"] * _["PRICE"])').run(order).as_text())
print('   0.10+0.20 =>', evaluate('0.10 + 0.20').as_text())

# 4 - reading results back -------------------------------------------------------
# A result is a Value: a scalar, children, both or neither.

print('4. reading results')
v = evaluate('SPLIT("a,b,c", ",")')
print('   size   =>', v.size())
print('   keys   =>', ','.join(v.keys()))
print('   [2]    =>', v.get('2').as_text())
print('   scalar =>', v.as_text())                # scalar context: first child
# A host bool prints differently in all five languages (true/1/True/T), and this
# file's output has to be byte-identical to its four siblings, so say it in SEL's
# own spelling rather than the host's.
print('   bool   =>', 'TRUE' if evaluate('1 < 2').as_bool() else 'FALSE')

# 5 - the context is mutated, so rules hand values back --------------------------

print('5. variables the rule set')
ctx = Value.from_native({'QTY': '3', 'PRICE': '19.99'})
compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT').run(ctx)
for name in ['NET', 'VAT', 'GROSS']:
    print(f'   {name:<5} =>', ctx.get(name).as_text())

# 6 - errors ----------------------------------------------------------------------
# Every failure carries a stable code and the position of the node that actually
# failed. Assert on .code, never on the message.

print('6. errors')
for src in ['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")']:
    try:
        evaluate(src)
        print(f'   {src:<17} => no error')
    except SelError as e:
        print(f'   {src:<17} => {e.code} at {e.line}:{e.col}')

# 7 - which fields does this rule read? -------------------------------------------
# Found statically, without running it. Wire these to your input listeners and
# re-validation is free.

print('7. dependencies')
print('  ', ' '.join(
    compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""').dependencies()))
