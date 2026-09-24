#!/usr/bin/env python3
"""An unnormalised export -- one wide table, from Python.

    tools/check-usage.sh sql-flat              (starts the databases for you)

order_export repeats the customer and the product on every line, the way a
spreadsheet or a nightly dump does, with the inconsistencies that come with
it: the same person under two spellings of their name and e-mail. The first
pipeline groups in SQL. The second normalises e-mails and counts distinct
customers, and the planner keeps the normalised values out of MariaDB's
hands: its collation would decide which of them are "the same", and SEL's
identity is exact bytes. The third explodes a `;`-separated column, which no
SQL step can express, so it runs in memory entirely.

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
    'EXPORT': relation('order_export', 'x', line_id='NUM', order_no='TEXT', order_date='TEXT',
                       customer_name='TEXT', customer_email='TEXT', customer_city='TEXT',
                       sku='TEXT', product_name='TEXT', category='TEXT', qty='NUM',
                       unit_price='NUM', tags='TEXT'),
}
# EXAMPLE-END bindings

PIPELINES = [
    ('revenue per city, February and March', 'revenue-per-city.sel'),
    ('distinct customers per city, by normalised e-mail', 'customers-per-city.sel'),
    ('lines per tag', 'lines-per-tag.sel'),
]

conn = connect('mariadb')

# The same tables in memory, for the comparison at the end of each pipeline.
tables = Value.none()
for name, table, key in [('EXPORT', 'order_export', 'line_id')]:
    tables.set(name, query(conn, f'SELECT * FROM {table} ORDER BY {key}'))

for n, (title, file) in enumerate(PIPELINES, 1):
    # EXAMPLE-BEGIN run
    with open(os.path.join(HERE, file), encoding='utf-8') as fh:
        program = compile(fh.read())
    plan = plan_hybrid(program, 'mariadb', SCHEMA)
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
