#!/usr/bin/env python3
"""The same report with no database at all -- generated data, in memory, from Python.

    PYTHONPATH=python python3 examples/memory-complex/python.py

examples/sql-complex loads the support desk from PostgreSQL. Here the same
rows come from examples/lib/tickets-generate.sel -- a SEL program that builds
them deterministically, and the source the PostgreSQL seed was rendered from
-- and the same report runs over them. Nothing below opens a connection:
the plan for MariaDB is computed from the bindings alone, and it says what it
said for PostgreSQL, that none of this report is SQL's to answer.

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

from sel import compile, evaluate                               # noqa: E402
from sel.sql import Binding, plan_hybrid                        # noqa: E402
from db import render                                           # noqa: E402


def read(name):
    with open(os.path.join(HERE, '..', 'lib', name), encoding='utf-8') as fh:
        return fh.read()


# EXAMPLE-BEGIN generate
data = evaluate(read('tickets-generate.sel'))
print('1. generated in memory')
for name in data.keys():
    print(f'   {name:<10}  {data.get(name).size():>3} rows')

report = compile(read('tickets-report.sel'))
print('2. the report')
print(render(report.run(data), '   | '))
# EXAMPLE-END generate

# Planning needs the schema, not a server: the bindings sql-complex describes
# PostgreSQL with, asked about MariaDB this time.
def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


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
plan = plan_hybrid(report, 'mariadb', SCHEMA)
print('3. planned for MariaDB, without connecting')
print('   plan       ', 'pure_memory' if plan.pure_memory else 'pushed down')
print('   reads      ', ', '.join(plan.source_tables))
