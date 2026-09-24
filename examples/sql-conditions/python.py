#!/usr/bin/env python3
"""SQL conditions -- one rule as a WHERE clause, from Python.

    tools/check-usage.sh sql-conditions        (starts the databases for you)

A rule written for the application can filter rows where they live. Part 1 is
the naive integration: the host knows nothing about the schema except that a
variable is a column of the same name. Part 2 describes the schema -- types,
a list of columns, a related table, a parameter -- and gets SQL that is both
tighter and able to say more. Either way a rule SQL cannot express is refused
whole, and runs in memory instead; every answer below is checked against the
in-memory one.

The four files beside this one print byte-identical output.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'lib'))
try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'python'))

from sel import Value, compile                                  # noqa: E402
from sel.sql import Binding, Sql, SqlError                      # noqa: E402
from db import connect, query                                   # noqa: E402


def ids(records):
    return ', '.join(record.get('id').as_text() for record in records) or '(none)'


def refusal(rule, dialect, bindings):
    """translate() says why try_translate() returned nothing."""
    try:
        Sql.translate(rule, dialect, bindings)
        return 'translated'
    except SqlError as e:
        return e.code


def context_of(row):
    """A row as a rule's context: SEL names are upper case, columns are not."""
    ctx = Value.none()
    for column, value in row.entries():
        ctx.set(column.upper(), value)
    return ctx


# 1 - naive: a column per variable, nothing else known ---------------------------

RULES = [
    'COUNTRY $== "PL" AND TIER $!= "standard"',
    'COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000',
    "RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)",
    'IS_BLANK(EMAIL) OR NOT RMATCH(\'^[^@ ]+@[^@ ]+$\', EMAIL)',
    'ANY(SPLIT(NAME, " "), LEN(_) > 9)',
]

print('1. naive bindings')
for dialect in ['sqlite', 'mariadb']:
    conn = connect(dialect)
    everyone = query(conn, 'SELECT * FROM customers ORDER BY id')
    for source in RULES:
        # EXAMPLE-BEGIN naive
        rule = compile(source)
        bindings = {name: Binding.column(name.lower()) for name in rule.dependencies()}
        where = Sql.try_translate(rule, dialect, bindings)
        if where is not None:
            sql = 'SELECT id FROM customers WHERE ' + where.as_condition('params') + ' ORDER BY id'
            rows = query(conn, sql, where.bindings())
        else:
            # refused: the rule stays in the application, over rows it loads
            rows = Value.none()
            for key, row in everyone.entries():
                if rule.run(context_of(row)).as_bool():
                    rows.set(key, row)
        # EXAMPLE-END naive
        in_memory = [row for row in everyone.values() if rule.run(context_of(row)).as_bool()]
        print(f'   {dialect:<8} {source}')
        print('            ', f'sql    {where.as_condition()}' if where
              else f'memory ({refusal(rule, dialect, bindings)})')
        print('             rows  ', ids(rows.values()),
              '| same as in memory:', 'TRUE' if ids(rows.values()) == ids(in_memory) else 'FALSE')

# 2 - involved: the host describes its schema ------------------------------------

print('2. described bindings')
conn = connect('postgresql')
# EXAMPLE-BEGIN involved
bindings = {
    'STATUS':    Binding.column('status', 'o', 'TEXT', exact=True),
    'TOTAL':     Binding.column('total', 'o', 'NUM'),
    'CHANNEL':   Binding.column('channel', 'o', 'TEXT', exact=True),
    'TAGS':      Binding.columns(Binding.column('tag1', 'o', 'TEXT'),
                                 Binding.column('tag2', 'o', 'TEXT'),
                                 Binding.column('tag3', 'o', 'TEXT')),
    'ITEMS':     Binding.relation('order_items', 'i', fields={
                     'sku':   Binding.column('sku', 'i', 'TEXT'),
                     'qty':   Binding.column('qty', 'i', 'NUM'),
                     'price': Binding.column('price', 'i', 'NUM'),
                 }, correlate='"i"."order_id" = "o"."id"'),
    'MIN_TOTAL': Binding.value(Value.text('100.00')),
}
# EXAMPLE-END involved

orders = query(conn, 'SELECT * FROM orders ORDER BY id')
items = query(conn, 'SELECT * FROM order_items ORDER BY order_id, line_no')


def order_context(order):
    """What the rule sees in memory: the same names, as values."""
    ctx = Value.none()
    for name in ['status', 'total', 'channel']:
        ctx.set(name.upper(), order.get(name))
    tags = Value.none()
    for n, column in enumerate(['tag1', 'tag2', 'tag3'], 1):
        tags.set(str(n), order.get(column))
    ctx.set('TAGS', tags)
    lines = Value.none()
    for item in items.values():
        if item.get('order_id').as_text() == order.get('id').as_text():
            lines.set(str(lines.size() + 1), item)
    ctx.set('ITEMS', lines)
    ctx.set('MIN_TOTAL', Value.text('100.00'))
    return ctx


for source in [
    'STATUS $== "paid" AND TOTAL >= MIN_TOTAL',
    'ANY(TAGS, _ $== "gift") AND CHANNEL $== "web"',
    'COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)',
    'SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL',
    'ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-")',
]:
    # EXAMPLE-BEGIN involved-run
    rule = compile(source)
    where = Sql.translate(rule, 'postgresql', bindings)
    sql = 'SELECT id FROM orders o WHERE ' + where.as_condition('params') + ' ORDER BY id'
    rows = query(conn, sql, where.bindings())
    # EXAMPLE-END involved-run
    in_memory = [o for o in orders.values() if rule.run(order_context(o)).as_bool()]
    print(f'   {source}')
    print(f'             sql    {where.as_condition()}')
    if where.bindings():
        print('             params', ', '.join(v.as_text() for v in where.bindings()))
    print('             rows  ', ids(rows.values()),
          '| same as in memory:', 'TRUE' if ids(rows.values()) == ids(in_memory) else 'FALSE')
