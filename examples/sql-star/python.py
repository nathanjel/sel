#!/usr/bin/env python3
"""A star schema -- whole pipelines in SQL, and split with memory, from Python.

    tools/check-usage.sh sql-star              (starts the databases for you)

fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
The application describes each table once, as a relation binding, and then
hands SEL whole pipelines. plan_hybrid() decides how much of each one the
database can answer: all of it (pure_sql), a prefix of it (hybrid, the rest
runs in memory over the rows the prefix returned), or none of it
(pure_memory). The answer is checked against a run of the same program over
the tables loaded into memory.

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
    'SALES':    relation('fact_sales', 's', sale_id='NUM', date_key='NUM', product_key='NUM',
                         store_key='NUM', qty='NUM', revenue='NUM'),
    'DATES':    relation('dim_date', 'd', date_key='NUM', year='NUM', quarter='NUM',
                         month='NUM', month_name='TEXT'),
    'STORES':   relation('dim_store', 't', store_key='NUM', city='TEXT', region='TEXT',
                         format='TEXT'),
    'PRODUCTS': relation('dim_product', 'p', product_key='NUM', sku='TEXT', name='TEXT',
                         category='TEXT', brand='TEXT', list_price='NUM'),
}
# EXAMPLE-END bindings

PIPELINES = [
    ('revenue by category, first quarter', 'revenue-by-category.sel'),
    ('best-selling product per region, stores only', 'best-product-per-region.sel'),
]

conn = connect('postgresql')

# The same tables in memory, for the comparison at the end of each pipeline.
tables = Value.none()
for name, table, key in [('SALES', 'fact_sales', 'sale_id'), ('DATES', 'dim_date', 'date_key'),
                         ('STORES', 'dim_store', 'store_key'),
                         ('PRODUCTS', 'dim_product', 'product_key')]:
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
