#!/usr/bin/env python3
"""SQL-aimed usage — pushing a rule down to the database, from Python.

    python3 examples/sql/python.py

The same rule that validates one order in the application can filter a million
of them in the database. What makes that safe is that the translation refuses
rather than guesses: if SQL cannot be made to mean what SEL means, no SQL is
emitted and the rule stays where it already worked.

The four files beside this one print byte-identical output;
tools/check-examples.sh diffs them.
"""

import os
import sys

try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '..', '..', 'python'))

from sel import compile                                         # noqa: E402
from sel.sql import Binding, Sql, SqlError                      # noqa: E402

# 1 - a rule, and what the database should call its inputs ---------------------
# dependencies() says exactly what has to be bound. A name the program reads and
# the bindings do not describe is a refusal, not a guess.

print('1. a rule pushed down')
rule = compile('TOTAL > 100.00 AND STATUS $== "open"')
print('   needs        =>', ' '.join(rule.dependencies()))

bindings = {
    'TOTAL':  Binding.column('total', 'o', 'NUM'),
    'STATUS': Binding.column('status', 'o', 'TEXT'),
}
frag = Sql.translate(rule, 'mariadb', bindings)
print('   sql          =>', frag.as_condition())

# 2 - the same rule as a prepared statement ------------------------------------
# Inline is for reading and for a query you build once. `params` is what you
# hand a driver: the literals become placeholders and bindings() gives the
# values in the order the placeholders appear in the output.

print('2. as parameters')
print('   sql          =>', frag.as_condition('params'))
print('   values       =>', ', '.join(v.dump() for v in frag.bindings()))

# 3 - one rule, every dialect --------------------------------------------------
# The differences below are the databases', not the rule's. Nothing in the
# program changed.

print('3. every dialect')
for dialect in Sql.dialects():
    print(f'   {dialect:<12} =>', Sql.translate(rule, dialect, bindings).as_condition())

# 4 - a rule over a related table ----------------------------------------------
# An aggregate over a relation becomes EXISTS / NOT EXISTS with a correlation,
# which is the shape a database can actually use an index for.

print('4. over a relation')
lines = compile('ALL(ITEMS, I, I["QTY"] > 0)')
print('   sql          =>', Sql.translate(lines, 'mariadb', {
    'ITEMS': Binding.relation('order_items', 'oi',
                              fields={'QTY': Binding.column('qty', type='NUM')},
                              correlate='`oi`.`order_id` = `o`.`id`'),
}).as_condition())

# 5 - refusal is an ordinary answer --------------------------------------------
# try_translate returns None so the caller can fall back to the evaluator
# without a try/except. translate() raises the same refusal with the reason
# written out, which is what you want in a build-time audit of a rule set.
#
# The line below says `tryTranslate` and `null` rather than `try_translate` and
# `None`: the four files beside this one must print the same bytes, so the label
# is the shared API's name and not this host's spelling of it.

print('5. refusal')
unbound = compile('MYSTERY > 1')
print('   tryTranslate =>', 'null — evaluate it in the host instead'
      if Sql.try_translate(unbound, 'mariadb', bindings) is None else 'translated')
try:
    Sql.translate(unbound, 'mariadb', bindings)
except SqlError as e:                   # anything else is a bug, and propagates
    print('   translate    =>', e.code)

# 6 - what a fragment knows about itself ---------------------------------------
# A caveat is the map saying "this dialect's answer may differ from SEL's here".
# An empty list is the layer promising it does not.

print('6. the fragment')
print('   kind         =>', frag.kind)
print('   dialect      =>', frag.dialect)
print('   exact        =>', 'TRUE' if len(frag.caveats) == 0 else 'FALSE')
