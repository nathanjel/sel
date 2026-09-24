// A star schema -- whole pipelines in SQL, and split with memory, from JavaScript.
//
//   tools/check-usage.sh sql-star              (starts the databases for you)
//
// fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
// The application describes each table once, as a relation binding, and then
// hands SEL whole pipelines. planHybrid() decides how much of each one the
// database can answer: all of it (pureSql), a prefix of it (hybrid, the rest
// runs in memory over the rows the prefix returned), or none of it
// (pureMemory). The answer is checked against a run of the same program over
// the tables loaded into memory.
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
  SALES:    relation('fact_sales', 's', { sale_id: 'NUM', date_key: 'NUM', product_key: 'NUM',
                                          store_key: 'NUM', qty: 'NUM', revenue: 'NUM' }),
  DATES:    relation('dim_date', 'd', { date_key: 'NUM', year: 'NUM', quarter: 'NUM',
                                        month: 'NUM', month_name: 'TEXT' }),
  STORES:   relation('dim_store', 't', { store_key: 'NUM', city: 'TEXT', region: 'TEXT',
                                         format: 'TEXT' }),
  PRODUCTS: relation('dim_product', 'p', { product_key: 'NUM', sku: 'TEXT', name: 'TEXT',
                                           category: 'TEXT', brand: 'TEXT', list_price: 'NUM' }),
};
// EXAMPLE-END bindings

const PIPELINES = [
  ['revenue by category, first quarter', 'revenue-by-category.sel'],
  ['best-selling product per region, stores only', 'best-product-per-region.sel'],
];

const conn = await connect('postgresql');

// The same tables in memory, for the comparison at the end of each pipeline.
const tables = Value.none();
for (const [name, table, key] of [['SALES', 'fact_sales', 'sale_id'], ['DATES', 'dim_date', 'date_key'],
  ['STORES', 'dim_store', 'store_key'], ['PRODUCTS', 'dim_product', 'product_key']]) {
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
