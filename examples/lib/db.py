"""A database runner for the SQL examples -- Python.

    from db import connect, query, runner

Every example that talks to a database goes through the three functions below,
and the four files beside this one do the same with their own drivers. The
contract is small on purpose, because it is what makes five hosts print the
same thing:

  - connect(dialect) opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in the
    environment (tools/check-usage.sh sets them).
  - query(conn, sql, params) runs a statement whose placeholders are `?` -- the
    spelling SEL's `params` mode emits -- and returns the rows as a SEL Value:
    a list of records, every column TEXT and SQL NULL as NULL. Money stays
    text, as it does everywhere in SEL; a float reaching here is an error.
  - runner(conn) is query() in the shape execute_hybrid() wants.
  - render(rows, pad) prints rows as `field=value` lines. It is written in SEL,
    so it prints the same bytes on every host by construction.

psycopg and PyMySQL want `%s`, not `?`, and read every `%` as their own
escape. placeholders() rewrites the statement for them: a `?` outside a quoted
literal or identifier is a placeholder (a numeric guard's regex literal has `?`
in it, which is why this cannot be a plain replace), and every `%` is doubled.
"""

import os
import sqlite3
from decimal import Decimal

from sel import Value, compile

ENV = os.environ.get


# EXAMPLE-BEGIN runner
def connect(dialect):
    if dialect == 'sqlite':
        return sqlite3.connect(ENV('SEL_DB_SQLITE_FILE'))
    common = dict(host=ENV('SEL_DB_HOST', '127.0.0.1'), user=ENV('SEL_DB_USER'),
                  password=ENV('SEL_DB_PASSWORD'))
    if dialect == 'postgresql':
        import psycopg
        return psycopg.connect(port=int(ENV('SEL_DB_POSTGRESQL_PORT')),
                               dbname=ENV('SEL_DB_NAME'), autocommit=True, **common)
    if dialect == 'mariadb':
        import pymysql
        return pymysql.connect(port=int(ENV('SEL_DB_MARIADB_PORT')),
                               database=ENV('SEL_DB_NAME'), autocommit=True, **common)
    raise ValueError(f'no runner for {dialect}')


def placeholders(sql, backslash_escapes):
    out, quote, i = [], None, 0
    while i < len(sql):
        c = sql[i]
        if quote:
            if c == '\\' and quote == "'" and backslash_escapes:
                out.append(sql[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == '?':
            c = '%s'
        out.append('%%' if c == '%' else c)
        i += 1
    return ''.join(out)


def query(conn, sql, params=()):
    if not isinstance(conn, sqlite3.Connection):
        sql = placeholders(sql, backslash_escapes=not hasattr(conn, 'pgconn'))
    cur = conn.cursor()
    cur.execute(sql, [None if p.is_null() else p.as_text() for p in params])
    names = [d[0] for d in cur.description]
    rows = Value.none()
    for n, row in enumerate(cur.fetchall(), 1):
        record = Value.none()
        for name, cell in zip(names, row):
            record.set(name, Value.null() if cell is None else Value.text(_text(cell)))
        rows.set(str(n), record)
    return rows


def _text(cell):
    if isinstance(cell, float):
        raise TypeError('a float reached SEL; declare the column DECIMAL or TEXT')
    return format(cell, 'f') if isinstance(cell, Decimal) else str(cell)


def runner(conn):
    return lambda sql, params: query(conn, sql, params)
# EXAMPLE-END runner


RENDER = compile('JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & "=" & (_ ?? "NULL")), "  ")), "\\n")')


def render(rows, pad=''):
    ctx = Value.none()
    ctx.set('ROWS', rows)
    ctx.set('PAD', Value.text(pad))
    return RENDER.run(ctx).as_text()
