#!/usr/bin/env python3
"""Adding a SQL flavour — teaching the translator about your database, from Python.

    python3 examples/dialect/python.py

The shipped map covers four targets over two bases. A deployment is rarely
exactly one of them: a driver wants numbered placeholders, a function is
spelled differently, an extension is not installed. A dialect is registered
rather than forked, so what you write is only the difference.

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
from sel.sql import Binding, Fragment, Sql, map                 # noqa: E402

rule = compile('NAME $== "ok" AND TOTAL > 10.00')
bindings = {
    'NAME':  Binding.column('name', 't', 'TEXT'),
    'TOTAL': Binding.column('total', 't', 'NUM'),
}


def sql_in(dialect):
    return Sql.translate(rule, dialect, bindings).as_condition('params')


# 1 - what ships ---------------------------------------------------------------

print('1. what ships')
print('   targets      =>', ' '.join(Sql.dialects()))
print('   postgresql   =>', ' -> '.join(map.chain('postgresql')))

# 2 - a flavour of your own -------------------------------------------------------
# `extends` is the whole mechanism: the new dialect answers for what it declares
# and defers upward for everything else. Two-phase lookup -- the whole overlay
# chain, then the whole shipped chain -- so an override never half-applies.

print('2. a flavour of your own')
map.define_dialect('pg-libpq', {
    'extends': 'postgresql',
    'version': '15',
    'target': True,                        # a base is not a target; this is a server
    'lexical': {'placeholder': '${n}'},    # libpq numbers its parameters
})
print('   targets      =>', ' '.join(Sql.dialects()))
print('   chain        =>', ' -> '.join(map.chain('pg-libpq')))
print('   base         =>', sql_in('postgresql'))
print('   pg-libpq     =>', sql_in('pg-libpq'))

# 3 - spelling one function differently ---------------------------------------------
# {*} is every argument; {0}, {1} pick them out. Note the slots are ZERO-based
# while every position SEL reports is one-based -- these are template holes, not
# SEL positions. The entry also says what it returns, because the translator
# infers kinds and will not guess.

print('3. one function, respelled')
map.define('pg-libpq', 'funcs', 'UPPER', {'tpl': 'UPPER({0} COLLATE "C")', 'ret': 'TEXT'})
print('   upper        =>',
      Sql.translate(compile('UPPER(NAME)'), 'pg-libpq', bindings).as_value())

# 4 - withdrawing what a deployment does not have ------------------------------------
# A None entry withdraws it. This is not the same as leaving it unmapped: it is
# the map saying "not here", and the rule is refused rather than emitted against
# a function the server does not have.

print('4. withdrawing an entry')
map.define('pg-libpq', 'funcs', 'RMATCH', None)
re = compile('RMATCH(\'^a\', NAME)')
print('   postgresql   =>', 'refused' if Sql.try_translate(re, 'postgresql', bindings)
      is None else 'translated')
print('   pg-libpq     =>', 'refused' if Sql.try_translate(re, 'pg-libpq', bindings)
      is None else 'translated')

# 5 - a builder, for what a template cannot say -----------------------------------------
# The escape hatch. It receives the emitter and the already-rendered arguments,
# and returns a fragment, so it can do what no string with holes in it can. The
# third argument is the call site -- a dict carrying `pos` -- and Python counts
# parameters, so a builder has to accept it even when it has no use for it.
#
# Splice the argument's `parts` rather than its rendered SQL. A part list is
# strings alternating with parameter slots, so splicing keeps a bound value
# bound; flattening it to a string first would inline whatever the argument
# carried and quietly turn a prepared statement back into concatenation.

print('5. a builder')
map.define_builder('pg-libpq', 'funcs', 'LEN', lambda emit, args, _at:
                   Fragment(['length(', *args[0].parts, ')'], 'NUM', emit.dialect()))
print('   len          =>',
      Sql.translate(compile('LEN(NAME)'), 'pg-libpq', bindings).as_value())

# 6 - putting it back ---------------------------------------------------------------------
# reset() drops every registration and leaves the shipped map. Worth knowing in
# a test suite: a registration that leaks into the next test is a test that
# passes for the wrong reason.

print('6. reset')
map.reset()
print('   targets      =>', ' '.join(Sql.dialects()))
print('   pg-libpq     =>', 'still there' if map.exists('pg-libpq') else 'gone')
