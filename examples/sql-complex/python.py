#!/usr/bin/env python3
"""A report no database can take a share of -- SQL loads, SEL computes, from Python.

    tools/check-usage.sh sql-complex           (starts the databases for you)

The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
numbers out of subjects with RGROUPS and searches the event log for each
ticket's first answer. plan_hybrid() finds no step of it PostgreSQL can
answer, and says so: pure_memory. So the database's job shrinks to handing
over the tables -- with the SELECTs written by SEL too, from the same bindings
-- and the report runs in memory over what came back. The last line checks it
against the report over the data generated in memory (examples/memory-complex),
which is where the rows in this database came from.

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

from sel import Value, compile, evaluate                        # noqa: E402
from sel.sql import Binding, Sql, plan_hybrid                   # noqa: E402
from db import connect, query, render                           # noqa: E402


def read(name):
    with open(os.path.join(HERE, '..', 'lib', name), encoding='utf-8') as fh:
        return fh.read()


def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


# EXAMPLE-BEGIN load
SCHEMA = {
    'TEAMS':     relation('teams', 'g', team_id='NUM', team='TEXT'),
    'CUSTOMERS': relation('customers', 'c', customer_id='NUM', customer='TEXT', plan='TEXT'),
    'SLA':       relation('sla', 's', plan='TEXT', priority='TEXT', respond_within='NUM',
                          resolve_within='NUM'),
    'TICKETS':   relation('tickets', 't', ticket_id='NUM', customer_id='NUM', team_id='NUM',
                          priority='TEXT', subject='TEXT', opened_at='NUM', closed_at='NUM'),
    'EVENTS':    relation('events', 'e', event_id='NUM', ticket_id='NUM', seq='NUM', at='NUM',
                          kind='TEXT', actor='TEXT'),
}
KEYS = {'TEAMS': 'team_id', 'CUSTOMERS': 'customer_id', 'SLA': 'plan',
        'TICKETS': 'ticket_id', 'EVENTS': 'event_id'}

report = compile(read('tickets-report.sel'))
plan = plan_hybrid(report, 'postgresql', SCHEMA)
print('1. the report, planned for PostgreSQL')
print('   plan       ', 'pure_memory' if plan.pure_memory else 'pushed down')
print('   reads      ', ', '.join(plan.source_tables))

print('2. so SQL only loads the tables it reads')
conn = connect('postgresql')
tables = Value.none()
for name in report.dependencies():
    load = compile(f'{name} .> SORT_BY(_["{KEYS[name]}"])')
    sql = Sql.translate_statement(load, 'postgresql', SCHEMA).as_statement()
    tables.set(name, query(conn, sql))
    print(f'   {name:<10}  {tables.get(name).size():>3} rows  {sql}')

print('3. and SEL computes the report over them')
result = report.run(tables)
print(render(result, '   | '))
# EXAMPLE-END load

generated = evaluate(read('tickets-generate.sel'))
print('   over the generated rows:',
      'same report' if report.run(generated).dump() == result.dump() else 'DIFFERENT')
