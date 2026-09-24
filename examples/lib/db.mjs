// A database runner for the SQL examples -- JavaScript.
//
//   import { connect, query, render, runner } from '../lib/db.mjs';
//
// Every example that talks to a database goes through the functions below,
// and the four files beside this one do the same with their own drivers. The
// contract is small on purpose, because it is what makes five hosts print the
// same thing:
//
//   - connect(dialect) opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in the
//     environment (tools/check-usage.sh sets them). close() it when done: an
//     open socket keeps node running.
//   - query(conn, sql, params) runs a statement whose placeholders are `?` -- the
//     spelling SEL's `params` mode emits -- and returns the rows as a SEL Value:
//     a list of records, every column TEXT and SQL NULL as NULL. Money stays
//     text, as it does everywhere in SEL; a float reaching here is an error.
//   - runner(conn, plan) is query() in the shape executeHybrid() wants.
//   - render(rows, pad) prints rows as `field=value` lines. It is written in SEL,
//     so it prints the same bytes on every host by construction.
//
// Every driver is asked for the database's own text: node-pg through a type
// parser that returns the string it was given, MariaDB through a typeCast that
// does the same, SQLite by reading integers as BigInt. Only a float is let
// through as a JS number, and text() refuses it.
//
// node:sqlite and the mariadb connector take `?` as it is. node-pg wants $1, $2,
// ... and placeholders() rewrites the statement for it: a `?` outside a quoted
// literal or identifier is a placeholder (a numeric guard's regex literal has
// `?` in it, which is why this cannot be a plain replace).
//
// pg and mariadb are not in this repository; they are found through NODE_PATH,
// which only require() honours -- hence createRequire.

import { createRequire } from 'node:module';
import { compile, Value } from '../../js/src/sel.mjs';

const ENV = process.env;
const require = createRequire(import.meta.url);

// EXAMPLE-BEGIN runner
// A connection is { run(sql, values) -> { names, rows }, close() }, whatever
// the driver underneath: rows are arrays, so columns keep their order.
export async function connect(dialect) {
  if (dialect === 'sqlite') {
    const { DatabaseSync } = await import('node:sqlite');
    const db = new DatabaseSync(ENV.SEL_DB_SQLITE_FILE);
    return {
      run: async (sql, values) => {
        const statement = db.prepare(sql);
        statement.setReadBigInts(true);
        statement.setReturnArrays(true);
        return { names: statement.columns().map((c) => c.name), rows: statement.all(...values) };
      },
      close: async () => db.close(),
    };
  }
  const common = { host: ENV.SEL_DB_HOST ?? '127.0.0.1', user: ENV.SEL_DB_USER,
                   password: ENV.SEL_DB_PASSWORD, database: ENV.SEL_DB_NAME };
  if (dialect === 'postgresql') {
    const { Client } = require('pg');
    const FLOATS = [700, 701];                        // float4, float8
    const client = new Client({ ...common, port: Number(ENV.SEL_DB_POSTGRESQL_PORT),
      types: { getTypeParser: (oid) => (FLOATS.includes(oid) ? Number : String) } });
    await client.connect();
    return {
      run: async (sql, values) => {
        const result = await client.query({ text: placeholders(sql), values, rowMode: 'array' });
        return { names: result.fields.map((f) => f.name), rows: result.rows };
      },
      close: () => client.end(),
    };
  }
  if (dialect === 'mariadb') {
    const mariadb = require('mariadb');
    const conn = await mariadb.createConnection({ ...common, port: Number(ENV.SEL_DB_MARIADB_PORT),
      rowsAsArray: true,
      typeCast: (column, next) => (['FLOAT', 'DOUBLE'].includes(column.type) ? next() : column.string()) });
    return {
      run: async (sql, values) => {
        const rows = await conn.query(sql, values);
        return { names: rows.meta.map((m) => m.name()), rows };
      },
      close: () => conn.end(),
    };
  }
  throw new Error(`no runner for ${dialect}`);
}

function placeholders(sql) {
  let out = '', quote = null, n = 0;
  for (const c of sql) {
    if (quote) {
      if (c === quote) quote = null;
    } else if ("'\"`".includes(c)) {
      quote = c;
    } else if (c === '?') {
      out += `$${++n}`;
      continue;
    }
    out += c;
  }
  return out;
}

export async function query(conn, sql, params = []) {
  const { names, rows } = await conn.run(sql, params.map((p) => (p.isNull() ? null : p.asText())));
  const out = Value.none();
  rows.forEach((row, n) => {
    const record = Value.none();
    names.forEach((name, i) => {
      record.set(name, row[i] === null ? Value.null() : Value.text(text(row[i])));
    });
    out.set(String(n + 1), record);
  });
  return out;
}

function text(cell) {
  if (typeof cell === 'number') {
    throw new TypeError('a float reached SEL; declare the column DECIMAL or TEXT');
  }
  return String(cell);
}

// executeHybrid() calls its runner synchronously, and node's drivers answer
// with a promise. A plan runs at most one statement, and the plan says which:
// so run it first, and hand executeHybrid a runner that returns those rows.
export async function runner(conn, plan) {
  const statement = plan.pureMemory ? null : plan.sqlStatement;
  const rows = statement && await query(conn, statement.asStatement('params'), statement.bindings());
  return () => rows;
}
// EXAMPLE-END runner

const RENDER = compile('JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & "=" & (_ ?? "NULL")), "  ")), "\\n")');

export function render(rows, pad = '') {
  const ctx = Value.none();
  ctx.set('ROWS', rows);
  ctx.set('PAD', Value.text(pad));
  return RENDER.run(ctx).asText();
}
