#!/usr/bin/env python3
"""An entity-attribute-value catalogue -- pipelines over EAV rows, from Python.

    tools/check-usage.sh sql-eav               (starts the databases for you)

entities holds one row per product; attributes holds one (entity, name, value)
row per property, every value TEXT, whatever it means. That shape is flexible
to write and awkward to ask: "red or blue, and made of steel" is two EXISTS
subqueries, and a price is a number only when the text says so. SEL pushes
down what SQLite can answer exactly (text equality, joins, grouping) and keeps
the rest -- pivoting attributes into records, comparing text as a number -- in
memory, where ISNUM can say what SQLite cannot.

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
    'PRODUCTS': relation('entities', 'e', id='NUM', sku='TEXT', kind='TEXT'),
    'ATTRS':    relation('attributes', 'a', entity_id='NUM', name='TEXT', value='TEXT'),
}
# EXAMPLE-END bindings

PIPELINES = [
    ('red or blue, and steel', 'red-or-blue-steel.sel'),
    ('products per colour', 'products-per-colour.sel'),
    ('priced under 60.00, pivoted', 'priced-under-60.sel'),
]

conn = connect('sqlite')

# The same tables in memory, for the comparison at the end of each pipeline.
tables = Value.none()
for name, table, key in [('PRODUCTS', 'entities', 'id'), ('ATTRS', 'attributes', 'entity_id, name')]:
    tables.set(name, query(conn, f'SELECT * FROM {table} ORDER BY {key}'))

for n, (title, file) in enumerate(PIPELINES, 1):
    # EXAMPLE-BEGIN run
    with open(os.path.join(HERE, file), encoding='utf-8') as fh:
        program = compile(fh.read())
    plan = plan_hybrid(program, 'sqlite', SCHEMA)
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
