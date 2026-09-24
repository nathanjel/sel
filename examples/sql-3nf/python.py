#!/usr/bin/env python3
"""A third-normal-form shop -- joins, grouping and a split, from Python.

    tools/check-usage.sh sql-3nf               (starts the databases for you)

categories, products, customers, orders and order_lines, each fact stored
once. The first pipeline is SQL from end to end. The second assigns every
customer to an A/B cohort by CRC32 of their e-mail -- the application's own
hashing, which PostgreSQL has no spelling for -- so the database joins, filters
and multiplies, and the cohorts are computed in memory over what it returned.

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
from sel.sql import Binding, execute_hybrid, plan_hybrid        # noqa: E402
from db import connect, query, render, runner                   # noqa: E402

# EXAMPLE-BEGIN bindings
def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


SCHEMA = {
    'CUSTOMERS': relation('customers', 'c', customer_id='NUM', name='TEXT', email='TEXT',
                          country='TEXT'),
    'ORDERS':    relation('orders', 'o', order_id='NUM', customer_id='NUM', status='TEXT',
                          ordered_on='TEXT'),
    'LINES':     relation('order_lines', 'l', order_id='NUM', line_no='NUM', product_id='NUM',
                          qty='NUM', unit_price='NUM'),
    'PRODUCTS':  relation('products', 'p', product_id='NUM', sku='TEXT', title='TEXT',
                          category_id='NUM', list_price='NUM'),
}
# EXAMPLE-END bindings

PIPELINES = [
    ('paid revenue per product since March', 'revenue-per-product.sel'),
    ('paid revenue per experiment cohort', 'revenue-per-cohort.sel'),
]

conn = connect('postgresql')

# The same tables in memory, for the comparison at the end of each pipeline.
tables = Value.none()
for name, table, key in [('CUSTOMERS', 'customers', 'customer_id'), ('ORDERS', 'orders', 'order_id'),
                         ('LINES', 'order_lines', 'order_id, line_no'),
                         ('PRODUCTS', 'products', 'product_id')]:
    tables.set(name, query(conn, f'SELECT * FROM {table} ORDER BY {key}'))

for n, (title, file) in enumerate(PIPELINES, 1):
    # EXAMPLE-BEGIN run
    with open(os.path.join(HERE, file), encoding='utf-8') as fh:
        program = compile(fh.read())
    plan = plan_hybrid(program, 'postgresql', SCHEMA)
    rows = execute_hybrid(plan, runner(conn), tables if plan.pure_memory else None)
    # EXAMPLE-END run
    kind = 'pure_sql' if plan.pure_sql else 'pure_memory' if plan.pure_memory else 'hybrid'
    print(f'{n}. {title}')
    print('   plan       ', kind)
    print('   reads      ', ', '.join(plan.source_tables))
    if plan.sql_statement is not None:
        print('   sql        ', plan.sql_statement.as_statement())
    print(render(rows, '   | '))
    print('   in memory  ', 'same rows' if program.run(tables.clone()).dump() == rows.dump()
          else 'DIFFERENT')
