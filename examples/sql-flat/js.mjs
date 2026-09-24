// An unnormalised export -- one wide table, from JavaScript.
//
//   tools/check-usage.sh sql-flat              (starts the databases for you)
//
// order_export repeats the customer and the product on every line, the way a
// spreadsheet or a nightly dump does, with the inconsistencies that come with
// it: the same person under two spellings of their name and e-mail. The first
// pipeline groups in SQL. The second normalises e-mails and counts distinct
// customers, and the planner keeps the normalised values out of MariaDB's
// hands: its collation would decide which of them are "the same", and SEL's
// identity is exact bytes. The third explodes a `;`-separated column, which no
// SQL step can express, so it runs in memory entirely.
//
// The four files beside this one print byte-identical output.

import { readFileSync } from 'node:fs';
import { compile, Value } from '../../js/src/sel.mjs';
import { Binding, executeHybrid, planHybrid } from '../../js/src/sql/index.mjs';
import { connect, query, render, runner } from '../lib/db.mjs';

// EXAMPLE-BEGIN bindings
function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  EXPORT: relation('order_export', 'x', { line_id: 'NUM', order_no: 'TEXT', order_date: 'TEXT',
                                          customer_name: 'TEXT', customer_email: 'TEXT',
                                          customer_city: 'TEXT', sku: 'TEXT', product_name: 'TEXT',
                                          category: 'TEXT', qty: 'NUM', unit_price: 'NUM',
                                          tags: 'TEXT' }),
};
// EXAMPLE-END bindings

const PIPELINES = [
  ['revenue per city, February and March', 'revenue-per-city.sel'],
  ['distinct customers per city, by normalised e-mail', 'customers-per-city.sel'],
  ['lines per tag', 'lines-per-tag.sel'],
];

const conn = await connect('mariadb');

// The same tables in memory, for the comparison at the end of each pipeline.
const tables = Value.none();
for (const [name, table, key] of [['EXPORT', 'order_export', 'line_id']]) {
  tables.set(name, await query(conn, `SELECT * FROM ${table} ORDER BY ${key}`));
}

for (const [i, [title, file]] of PIPELINES.entries()) {
  // EXAMPLE-BEGIN run
  const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
  const plan = planHybrid(program, 'mariadb', SCHEMA);
  const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
  // EXAMPLE-END run
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log(`${i + 1}. ${title}`);
  console.log('   plan       ', kind);
  console.log('   reads      ', plan.sourceTables.join(', '));
  if (plan.sqlStatement !== null) console.log('   sql        ', plan.sqlStatement.asStatement());
  console.log(render(rows, '   | '));
  console.log('   in memory  ', program.run(tables.clone()).dump() === rows.dump() ? 'same rows' : 'DIFFERENT');
}
await conn.close();
