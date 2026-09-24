// A third-normal-form shop -- joins, grouping and a split, from JavaScript.
//
//   tools/check-usage.sh sql-3nf               (starts the databases for you)
//
// categories, products, customers, orders and order_lines, each fact stored
// once. The first pipeline is SQL from end to end. The second assigns every
// customer to an A/B cohort by CRC32 of their e-mail -- the application's own
// hashing, which PostgreSQL has no spelling for -- so the database joins, filters
// and multiplies, and the cohorts are computed in memory over what it returned.
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
  CUSTOMERS: relation('customers', 'c', { customer_id: 'NUM', name: 'TEXT', email: 'TEXT',
                                          country: 'TEXT' }),
  ORDERS:    relation('orders', 'o', { order_id: 'NUM', customer_id: 'NUM', status: 'TEXT',
                                       ordered_on: 'TEXT' }),
  LINES:     relation('order_lines', 'l', { order_id: 'NUM', line_no: 'NUM', product_id: 'NUM',
                                            qty: 'NUM', unit_price: 'NUM' }),
  PRODUCTS:  relation('products', 'p', { product_id: 'NUM', sku: 'TEXT', title: 'TEXT',
                                         category_id: 'NUM', list_price: 'NUM' }),
};
// EXAMPLE-END bindings

const PIPELINES = [
  ['paid revenue per product since March', 'revenue-per-product.sel'],
  ['paid revenue per experiment cohort', 'revenue-per-cohort.sel'],
];

const conn = await connect('postgresql');

// The same tables in memory, for the comparison at the end of each pipeline.
const tables = Value.none();
for (const [name, table, key] of [['CUSTOMERS', 'customers', 'customer_id'], ['ORDERS', 'orders', 'order_id'],
  ['LINES', 'order_lines', 'order_id, line_no'], ['PRODUCTS', 'products', 'product_id']]) {
  tables.set(name, await query(conn, `SELECT * FROM ${table} ORDER BY ${key}`));
}

for (const [i, [title, file]] of PIPELINES.entries()) {
  // EXAMPLE-BEGIN run
  const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
  const plan = planHybrid(program, 'postgresql', SCHEMA);
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
