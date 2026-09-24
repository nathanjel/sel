#!/usr/bin/env python3
"""The application's own functions, in memory and in PostgreSQL -- Python.

    tools/check-usage.sh sql-functions         (starts the databases for you)

The application registers five functions of its own. Each has a local
implementation -- the code register_function runs -- and four also get a SQL
spelling for PostgreSQL, which the application promises computes the same
thing (spec §8.1, sql/MAP.md §4.7):

  SLUG(title)                 a plain value mapping        -> slug(), an SQL function
  MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
  VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
  SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
  HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
  WORDS(title)                returns a list               -> no spelling: stays in memory

Every pipeline prints its plan and its rows, and whether those rows are the rows
the same program computes in memory -- which is how the example checks that the
two implementations of each function agree on this data.

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

from sel import Value, compile, register_function               # noqa: E402
from sel.sql import (Binding, Sql, SqlError, execute_hybrid,    # noqa: E402
                     map, plan_hybrid)
from db import connect, query, render, runner                   # noqa: E402

conn = connect('postgresql')

# 1 - the local implementations ------------------------------------------------------
# What register_function runs: plain code, or -- where exact decimal arithmetic
# matters -- a SEL expression, so the local answer has SEL's numbers.

# EXAMPLE-BEGIN local
def slug(args):
    out, dash = [], False
    for c in args.text(0):
        c = chr(ord(c) + 32) if 'A' <= c <= 'Z' else c          # ASCII only, as SQL's
        if 'a' <= c <= 'z' or '0' <= c <= '9':                  # [^a-z0-9]+ sees it
            if dash and out:
                out.append('-')
            out.append(c)
            dash = False
        else:
            dash = True
    return Value.text(''.join(out))


MARGIN = compile('ROUND((PRICE - COST) * 100 / PRICE, 1)')


def margin_pct(args):
    ctx = Value.none()
    ctx.set('PRICE', args.val(0))
    ctx.set('COST', args.val(1))
    return MARGIN.run(ctx)


RATES = {(r.get('country').as_text(), r.get('category').as_text()): r.get('rate')
         for r in query(conn, 'SELECT * FROM vat_rates').values()}


def vat_rate(args):
    country, category = args.text(0), args.text(1)
    rate = RATES.get((country, category))
    if rate is None:                          # not `or`: a Value with no children is falsy
        rate = RATES.get((country, '*'))
    return rate.clone() if rate is not None else Value.text('0')


SHIPPING = compile('COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)'
                   ' * IF(COUNTRY $== "PL", 1, 2)')


def shipping_cost(args):
    ctx = Value.none()
    ctx.set('KG', args.val(0))
    ctx.set('COUNTRY', args.val(1))
    return SHIPPING.run(ctx)


def has_tag(args):
    tags, tag = args.val(0), args.text(1)
    values = tags.values() if tags.size() > 0 else [tags]      # a scalar is a list of one
    return Value.bool(any(v.as_text() == tag for v in values))


def words(args):
    out = Value.none()
    for w in slug(args).as_text().split('-'):
        if w:
            out.set(str(out.size() + 1), Value.text(w))
    return out


register_function('SLUG', 1, 1, slug)
register_function('MARGIN_PCT', 2, 2, margin_pct)
register_function('VAT_RATE', 2, 2, vat_rate)
register_function('SHIPPING_COST', 2, 2, shipping_cost)
register_function('HAS_TAG', 2, 2, has_tag)
register_function('WORDS', 1, 1, words)
# EXAMPLE-END local

# 2 - the SQL spellings ------------------------------------------------------------------
# After the functions: a spelling for a name that is not registered is refused.

# EXAMPLE-BEGIN spell
map.define('postgresql', 'funcs', 'SLUG',
           {'tpl': 'slug({0})', 'ret': 'TEXT', 'args': ['TEXT']})
map.define('postgresql', 'funcs', 'MARGIN_PCT',
           {'tpl': 'margin_pct({0}, {1})', 'ret': 'NUM', 'args': ['NUM', 'NUM']})
map.define('postgresql', 'funcs', 'VAT_RATE',
           {'tpl': 'vat_rate({0}, {1})', 'ret': 'NUM', 'args': ['TEXT', 'TEXT']})
map.define('postgresql', 'funcs', 'SHIPPING_COST',
           {'tpl': 'shipping_cost({0}, {1})', 'ret': 'NUM', 'args': ['NUM', 'TEXT']})
map.define('postgresql', 'funcs', 'HAS_TAG',
           {'tpl': '({1} = ANY(ARRAY[{0}]))', 'ret': 'BOOL', 'args': ['LIST', 'TEXT']})
# WORDS returns a list: no spelling can say that, so it has none.
# EXAMPLE-END spell


def relation(table, alias, **fields):
    return Binding.relation(table, alias, fields={
        name: Binding.column(name, alias, kind) for name, kind in fields.items()})


SCHEMA = {
    'PRODUCTS': relation('products', 'p', product_id='NUM', title='TEXT', category='TEXT',
                         price='NUM', cost='NUM', weight_kg='NUM',
                         tag1='TEXT', tag2='TEXT', tag3='TEXT'),
    'ORDERS':   relation('orders', 'o', order_id='NUM', country='TEXT'),
    'LINES':    relation('order_lines', 'l', order_id='NUM', line_no='NUM', product_id='NUM',
                         qty='NUM'),
}

tables = Value.none()
for name, table, key in [('PRODUCTS', 'products', 'product_id'), ('ORDERS', 'orders', 'order_id'),
                         ('LINES', 'order_lines', 'order_id, line_no')]:
    tables.set(name, query(conn, f'SELECT * FROM {table} ORDER BY {key}'))

PIPELINES = [
    ('gifts with a margin of 40% or more', 'gifts-by-margin.sel'),
    ('gross revenue and shipping per country', 'gross-per-country.sel'),
    ('words in the titles of the better-margin products', 'title-words.sel'),
]

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
    if plan.sql_statement is not None:
        print('   sql        ', plan.sql_statement.as_statement())
        print('   caveats    ', ', '.join(plan.sql_statement.caveats) or '(none)')
    print(render(rows, '   | '))
    print('   in memory  ', 'same rows' if program.run(tables.clone()).dump() == rows.dump()
          else 'DIFFERENT')

# 4 - what strict translation says ---------------------------------------------------------
# A spelling is the application's promise, not this layer's, so strict mode --
# exact or nothing -- refuses it.

print(f'{len(PIPELINES) + 1}. strict translation')
# EXAMPLE-BEGIN strict
rule = compile('SLUG(TITLE) $== "cast-iron-pan"')
title = {'TITLE': Binding.column('title', 'p', 'TEXT')}
print('   caveats    ', ', '.join(Sql.translate(rule, 'postgresql', title).caveats))
try:
    Sql.translate(rule, 'postgresql', title, {'strict': True})
except SqlError as e:
    print('   strict     ', e.code)
# EXAMPLE-END strict
