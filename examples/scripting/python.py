#!/usr/bin/env python3
"""Scripting an application's behaviour -- functions the host provides, from Python.

    PYTHONPATH=python python3 examples/scripting/python.py

SEL has no way to reach the world on its own, and that is the point: the
application decides what a script may touch by registering functions. Here
the warehouse gets four -- STOCK and WEIGHT to read the catalogue, RESERVE to
take stock, NOTIFY to queue a message -- and fulfil.sel, a file the warehouse
team owns, decides per order whether to ship, how, and whom to tell. The
application stays the same when the policy changes.

A registered function is strict: its arguments arrive evaluated, left to
right, through the same typed readers the builtins use, so a script passing
the wrong kind gets the usual error at the usual position.

The four files beside this one print byte-identical output.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'python'))

from sel import SelError, Value, compile, register_function     # noqa: E402

inventory = {'LAMP-01': 4, 'DESK-02': 1, 'CHAIR-03': 6}
weights = {'LAMP-01': '1.6', 'DESK-02': '28.0', 'CHAIR-03': '7.5'}
outbox = []

# EXAMPLE-BEGIN register
def stock(args):
    return Value.int(inventory.get(args.text(0), 0))


def reserve(args):
    sku, qty = args.text(0), args.non_neg_int(1)
    if inventory.get(sku, 0) < qty:
        return Value.bool(False)
    inventory[sku] -= qty
    return Value.bool(True)


def weight(args):
    return Value.text(weights.get(args.text(0), '0'))


def notify(args):
    outbox.append(f'{args.text(0)}: {args.text(1)}')
    return Value.bool(True)


register_function('STOCK', 1, 1, stock)
register_function('RESERVE', 2, 2, reserve)
register_function('WEIGHT', 1, 1, weight)
register_function('NOTIFY', 2, 2, notify)
# EXAMPLE-END register

# EXAMPLE-BEGIN run
with open(os.path.join(HERE, 'fulfil.sel'), encoding='utf-8') as fh:
    fulfil = compile(fh.read())             # after registering: names resolve now

print('1. the script reads', ', '.join(fulfil.dependencies()))
print('2. orders')
orders = [
    {'ORDER': 'A-1', 'COUNTRY': 'PL', 'ITEMS': [{'sku': 'LAMP-01', 'qty': '2'},
                                               {'sku': 'CHAIR-03', 'qty': '1'}]},
    {'ORDER': 'A-2', 'COUNTRY': 'DE', 'ITEMS': [{'sku': 'DESK-02', 'qty': '1'},
                                               {'sku': 'CHAIR-03', 'qty': '2'}]},
    {'ORDER': 'A-3', 'COUNTRY': 'PL', 'ITEMS': [{'sku': 'LAMP-01', 'qty': '3'}]},
    {'ORDER': 'A-4', 'COUNTRY': 'PL', 'ITEMS': [{'sku': 'LAMP-01', 'qty': 'two'}]},
]
for order in orders:
    try:
        decision = fulfil.run(Value.from_native(order)).as_text()
    except SelError as e:
        decision = f'{e.code} at {e.line}:{e.col}'
    print(f'   {order["ORDER"]}  {decision}')
# EXAMPLE-END run

print('3. outbox')
for message in outbox:
    print('  ', message)
print('4. stock left')
for sku in sorted(inventory):
    print(f'   {sku:<9} {inventory[sku]}')
