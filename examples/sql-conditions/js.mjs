// SQL conditions -- one rule as a WHERE clause, from JavaScript.
//
//   tools/check-usage.sh sql-conditions        (starts the databases for you)
//
// A rule written for the application can filter rows where they live. Part 1 is
// the naive integration: the host knows nothing about the schema except that a
// variable is a column of the same name. Part 2 describes the schema -- types,
// a list of columns, a related table, a parameter -- and gets SQL that is both
// tighter and able to say more. Either way a rule SQL cannot express is refused
// whole, and runs in memory instead; every answer below is checked against the
// in-memory one.
//
// The four files beside this one print byte-identical output.

import { compile, Value } from '../../js/src/sel.mjs';
import { Binding, Sql, SqlError } from '../../js/src/sql/index.mjs';
import { connect, query } from '../lib/db.mjs';

function ids(records) {
  return records.map((record) => record.get('id').asText()).join(', ') || '(none)';
}

// translate() says why tryTranslate() returned null.
function refusal(rule, dialect, bindings) {
  try {
    Sql.translate(rule, dialect, bindings);
    return 'translated';
  } catch (e) {
    if (!(e instanceof SqlError)) throw e;
    return e.code;
  }
}

// A row as a rule's context: SEL names are upper case, columns are not.
function contextOf(row) {
  const ctx = Value.none();
  for (const [column, value] of row.entries()) ctx.set(column.toUpperCase(), value);
  return ctx;
}

// 1 - naive: a column per variable, nothing else known ---------------------------

const RULES = [
  'COUNTRY $== "PL" AND TIER $!= "standard"',
  'COUNTRY $== "PL" AND CREDIT_LIMIT >= 1000',
  "RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)",
  'IS_BLANK(EMAIL) OR NOT RMATCH(\'^[^@ ]+@[^@ ]+$\', EMAIL)',
  'ANY(SPLIT(NAME, " "), LEN(_) > 9)',
];

console.log('1. naive bindings');
for (const dialect of ['sqlite', 'mariadb']) {
  const conn = await connect(dialect);
  const everyone = await query(conn, 'SELECT * FROM customers ORDER BY id');
  for (const source of RULES) {
    // EXAMPLE-BEGIN naive
    const rule = compile(source);
    const bindings = Object.fromEntries(
      rule.dependencies().map((name) => [name, Binding.column(name.toLowerCase())]));
    const where = Sql.tryTranslate(rule, dialect, bindings);
    let rows;
    if (where !== null) {
      const sql = 'SELECT id FROM customers WHERE ' + where.asCondition('params') + ' ORDER BY id';
      rows = await query(conn, sql, where.bindings());
    } else {
      // refused: the rule stays in the application, over rows it loads
      rows = Value.none();
      for (const [key, row] of everyone.entries()) {
        if (rule.run(contextOf(row)).asBool()) rows.set(key, row);
      }
    }
    // EXAMPLE-END naive
    const inMemory = everyone.values().filter((row) => rule.run(contextOf(row)).asBool());
    console.log(`   ${dialect.padEnd(8)} ${source}`);
    console.log('            ', where !== null ? `sql    ${where.asCondition()}`
      : `memory (${refusal(rule, dialect, bindings)})`);
    console.log('             rows  ', ids(rows.values()),
      '| same as in memory:', ids(rows.values()) === ids(inMemory) ? 'TRUE' : 'FALSE');
  }
  await conn.close();
}

// 2 - involved: the host describes its schema ------------------------------------

console.log('2. described bindings');
const conn = await connect('postgresql');
// EXAMPLE-BEGIN involved
const bindings = {
  STATUS:    Binding.column('status', 'o', 'TEXT', /* exact */ true),
  TOTAL:     Binding.column('total', 'o', 'NUM'),
  CHANNEL:   Binding.column('channel', 'o', 'TEXT', /* exact */ true),
  TAGS:      Binding.columns(Binding.column('tag1', 'o', 'TEXT'),
                             Binding.column('tag2', 'o', 'TEXT'),
                             Binding.column('tag3', 'o', 'TEXT')),
  ITEMS:     Binding.relation('order_items', 'i', {
               sku:   Binding.column('sku', 'i', 'TEXT'),
               qty:   Binding.column('qty', 'i', 'NUM'),
               price: Binding.column('price', 'i', 'NUM'),
             }, /* scalar */ null, /* correlate */ '"i"."order_id" = "o"."id"'),
  MIN_TOTAL: Binding.value(Value.text('100.00')),
};
// EXAMPLE-END involved

const orders = await query(conn, 'SELECT * FROM orders ORDER BY id');
const items = await query(conn, 'SELECT * FROM order_items ORDER BY order_id, line_no');

// What the rule sees in memory: the same names, as values.
function orderContext(order) {
  const ctx = Value.none();
  for (const name of ['status', 'total', 'channel']) ctx.set(name.toUpperCase(), order.get(name));
  const tags = Value.none();
  ['tag1', 'tag2', 'tag3'].forEach((column, i) => tags.set(String(i + 1), order.get(column)));
  ctx.set('TAGS', tags);
  const lines = Value.none();
  for (const item of items.values()) {
    if (item.get('order_id').asText() === order.get('id').asText()) {
      lines.set(String(lines.size() + 1), item);
    }
  }
  ctx.set('ITEMS', lines);
  ctx.set('MIN_TOTAL', Value.text('100.00'));
  return ctx;
}

for (const source of [
  'STATUS $== "paid" AND TOTAL >= MIN_TOTAL',
  'ANY(TAGS, _ $== "gift") AND CHANNEL $== "web"',
  'COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)',
  'SUM(ITEMS, I, I["qty"] * I["price"]) != TOTAL',
  'ANY(ITEMS, I, LEFT(I["sku"], 3) $== "GM-")',
]) {
  // EXAMPLE-BEGIN involved-run
  const rule = compile(source);
  const where = Sql.translate(rule, 'postgresql', bindings);
  const sql = 'SELECT id FROM orders o WHERE ' + where.asCondition('params') + ' ORDER BY id';
  const rows = await query(conn, sql, where.bindings());
  // EXAMPLE-END involved-run
  const inMemory = orders.values().filter((o) => rule.run(orderContext(o)).asBool());
  console.log(`   ${source}`);
  console.log(`             sql    ${where.asCondition()}`);
  if (where.bindings().length > 0) {
    console.log('             params', where.bindings().map((v) => v.asText()).join(', '));
  }
  console.log('             rows  ', ids(rows.values()),
    '| same as in memory:', ids(rows.values()) === ids(inMemory) ? 'TRUE' : 'FALSE');
}
await conn.close();
