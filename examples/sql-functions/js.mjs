// The application's own functions, in memory and in PostgreSQL -- JavaScript.
//
//   tools/check-usage.sh sql-functions         (starts the databases for you)
//
// The application registers five functions of its own. Each has a local
// implementation -- the code registerFunction runs -- and four also get a SQL
// spelling for PostgreSQL, which the application promises computes the same
// thing (spec §8.1, sql/MAP.md §4.7):
//
//   SLUG(title)                 a plain value mapping        -> slug(), an SQL function
//   MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
//   VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
//   SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
//   HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
//   WORDS(title)                returns a list               -> no spelling: stays in memory
//
// Every pipeline prints its plan and its rows, and whether those rows are the rows
// the same program computes in memory -- which is how the example checks that the
// two implementations of each function agree on this data.
//
// The four files beside this one print byte-identical output.

import { readFileSync } from 'node:fs';
import { compile, registerFunction, Value } from '../../js/src/sel.mjs';
import { Binding, Sql, SqlError, executeHybrid, map, planHybrid } from '../../js/src/sql/index.mjs';
import { connect, query, render, runner } from '../lib/db.mjs';

const conn = await connect('postgresql');

// 1 - the local implementations ------------------------------------------------------
// What registerFunction runs: plain code, or -- where exact decimal arithmetic
// matters -- a SEL expression, so the local answer has SEL's numbers.

// EXAMPLE-BEGIN local
function slug(args) {
  const out = [];
  let dash = false;
  for (let c of args.text(0)) {
    if (c >= 'A' && c <= 'Z') c = String.fromCharCode(c.charCodeAt(0) + 32);  // ASCII only, as SQL's
    if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {                   // [^a-z0-9]+ sees it
      if (dash && out.length > 0) out.push('-');
      out.push(c);
      dash = false;
    } else {
      dash = true;
    }
  }
  return Value.text(out.join(''));
}

const MARGIN = compile('ROUND((PRICE - COST) * 100 / PRICE, 1)');

function marginPct(args) {
  const ctx = Value.none();
  ctx.set('PRICE', args.val(0));
  ctx.set('COST', args.val(1));
  return MARGIN.run(ctx);
}

const RATES = new Map();
for (const r of (await query(conn, 'SELECT * FROM vat_rates')).values()) {
  RATES.set(`${r.get('country').asText()}/${r.get('category').asText()}`, r.get('rate'));
}

function vatRate(args) {
  const country = args.text(0), category = args.text(1);
  const rate = RATES.get(`${country}/${category}`) ?? RATES.get(`${country}/*`);
  return rate !== undefined ? rate.clone() : Value.text('0');
}

const SHIPPING = compile('COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)'
  + ' * IF(COUNTRY $== "PL", 1, 2)');

function shippingCost(args) {
  const ctx = Value.none();
  ctx.set('KG', args.val(0));
  ctx.set('COUNTRY', args.val(1));
  return SHIPPING.run(ctx);
}

function hasTag(args) {
  const tags = args.val(0), tag = args.text(1);
  const values = tags.size() > 0 ? tags.values() : [tags];      // a scalar is a list of one
  return Value.bool(values.some((v) => v.asText() === tag));
}

function words(args) {
  const out = Value.none();
  for (const w of slug(args).asText().split('-')) {
    if (w !== '') out.set(String(out.size() + 1), Value.text(w));
  }
  return out;
}

registerFunction('SLUG', 1, 1, slug);
registerFunction('MARGIN_PCT', 2, 2, marginPct);
registerFunction('VAT_RATE', 2, 2, vatRate);
registerFunction('SHIPPING_COST', 2, 2, shippingCost);
registerFunction('HAS_TAG', 2, 2, hasTag);
registerFunction('WORDS', 1, 1, words);
// EXAMPLE-END local

// 2 - the SQL spellings ------------------------------------------------------------------
// After the functions: a spelling for a name that is not registered is refused.

// EXAMPLE-BEGIN spell
map.define('postgresql', 'funcs', 'SLUG',
  { tpl: 'slug({0})', ret: 'TEXT', args: ['TEXT'] });
map.define('postgresql', 'funcs', 'MARGIN_PCT',
  { tpl: 'margin_pct({0}, {1})', ret: 'NUM', args: ['NUM', 'NUM'] });
map.define('postgresql', 'funcs', 'VAT_RATE',
  { tpl: 'vat_rate({0}, {1})', ret: 'NUM', args: ['TEXT', 'TEXT'] });
map.define('postgresql', 'funcs', 'SHIPPING_COST',
  { tpl: 'shipping_cost({0}, {1})', ret: 'NUM', args: ['NUM', 'TEXT'] });
map.define('postgresql', 'funcs', 'HAS_TAG',
  { tpl: '({1} = ANY(ARRAY[{0}]))', ret: 'BOOL', args: ['LIST', 'TEXT'] });
// WORDS returns a list: no spelling can say that, so it has none.
// EXAMPLE-END spell

function relation(table, alias, fields) {
  return Binding.relation(table, alias, Object.fromEntries(Object.entries(fields)
    .map(([name, kind]) => [name, Binding.column(name, alias, kind)])));
}

const SCHEMA = {
  PRODUCTS: relation('products', 'p', { product_id: 'NUM', title: 'TEXT', category: 'TEXT',
                                        price: 'NUM', cost: 'NUM', weight_kg: 'NUM',
                                        tag1: 'TEXT', tag2: 'TEXT', tag3: 'TEXT' }),
  ORDERS:   relation('orders', 'o', { order_id: 'NUM', country: 'TEXT' }),
  LINES:    relation('order_lines', 'l', { order_id: 'NUM', line_no: 'NUM', product_id: 'NUM',
                                           qty: 'NUM' }),
};

const tables = Value.none();
for (const [name, table, key] of [['PRODUCTS', 'products', 'product_id'], ['ORDERS', 'orders', 'order_id'],
  ['LINES', 'order_lines', 'order_id, line_no']]) {
  tables.set(name, await query(conn, `SELECT * FROM ${table} ORDER BY ${key}`));
}

const PIPELINES = [
  ['gifts with a margin of 40% or more', 'gifts-by-margin.sel'],
  ['gross revenue and shipping per country', 'gross-per-country.sel'],
  ['words in the titles of the better-margin products', 'title-words.sel'],
];

for (const [i, [title, file]] of PIPELINES.entries()) {
  // EXAMPLE-BEGIN run
  const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
  const plan = planHybrid(program, 'postgresql', SCHEMA);
  const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
  // EXAMPLE-END run
  const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
  console.log(`${i + 1}. ${title}`);
  console.log('   plan       ', kind);
  if (plan.sqlStatement !== null) {
    console.log('   sql        ', plan.sqlStatement.asStatement());
    console.log('   caveats    ', plan.sqlStatement.caveats.join(', ') || '(none)');
  }
  console.log(render(rows, '   | '));
  console.log('   in memory  ', program.run(tables.clone()).dump() === rows.dump() ? 'same rows' : 'DIFFERENT');
}
await conn.close();

// 4 - what strict translation says ---------------------------------------------------------
// A spelling is the application's promise, not this layer's, so strict mode --
// exact or nothing -- refuses it.

console.log(`${PIPELINES.length + 1}. strict translation`);
// EXAMPLE-BEGIN strict
const rule = compile('SLUG(TITLE) $== "cast-iron-pan"');
const title = { TITLE: Binding.column('title', 'p', 'TEXT') };
console.log('   caveats    ', Sql.translate(rule, 'postgresql', title).caveats.join(', '));
try {
  Sql.translate(rule, 'postgresql', title, { strict: true });
} catch (e) {
  if (!(e instanceof SqlError)) throw e;
  console.log('   strict     ', e.code);
}
// EXAMPLE-END strict
