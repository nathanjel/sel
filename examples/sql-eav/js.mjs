// An entity-attribute-value catalogue -- pipelines over EAV rows, from JavaScript.
//
//   tools/check-usage.sh sql-eav               (starts the databases for you)
//
// entities holds one row per product; attributes holds one (entity, name, value)
// row per property, every value TEXT, whatever it means. That shape is flexible
// to write and awkward to ask: "red or blue, and made of steel" is two EXISTS
// subqueries, and a price is a number only when the text says so. SEL pushes
// down what SQLite can answer exactly (text equality, joins, grouping) and keeps
// the rest -- pivoting attributes into records, comparing text as a number -- in
// memory, where ISNUM can say what SQLite cannot.
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
  PRODUCTS: relation('entities', 'e', { id: 'NUM', sku: 'TEXT', kind: 'TEXT' }),
  ATTRS:    relation('attributes', 'a', { entity_id: 'NUM', name: 'TEXT', value: 'TEXT' }),
};
// EXAMPLE-END bindings

const PIPELINES = [
  ['red or blue, and steel', 'red-or-blue-steel.sel'],
  ['products per colour', 'products-per-colour.sel'],
  ['priced under 60.00, pivoted', 'priced-under-60.sel'],
];

const conn = await connect('sqlite');

// The same tables in memory, for the comparison at the end of each pipeline.
const tables = Value.none();
for (const [name, table, key] of [['PRODUCTS', 'entities', 'id'],
  ['ATTRS', 'attributes', 'entity_id, name']]) {
  tables.set(name, await query(conn, `SELECT * FROM ${table} ORDER BY ${key}`));
}

for (const [i, [title, file]] of PIPELINES.entries()) {
  // EXAMPLE-BEGIN run
  const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
  const plan = planHybrid(program, 'sqlite', SCHEMA);
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
