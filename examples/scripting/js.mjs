// Scripting an application's behaviour -- functions the host provides, from JavaScript.
//
//   node examples/scripting/js.mjs
//
// SEL has no way to reach the world on its own, and that is the point: the
// application decides what a script may touch by registering functions. Here
// the warehouse gets four -- STOCK and WEIGHT to read the catalogue, RESERVE to
// take stock, NOTIFY to queue a message -- and fulfil.sel, a file the warehouse
// team owns, decides per order whether to ship, how, and whom to tell. The
// application stays the same when the policy changes.
//
// A registered function is strict: its arguments arrive evaluated, left to
// right, through the same typed readers the builtins use, so a script passing
// the wrong kind gets the usual error at the usual position.
//
// The four files beside this one print byte-identical output.

import { readFileSync } from 'node:fs';
import { compile, registerFunction, SelError, Value } from '../../js/src/sel.mjs';

const inventory = new Map([['LAMP-01', 4], ['DESK-02', 1], ['CHAIR-03', 6]]);
const weights = new Map([['LAMP-01', '1.6'], ['DESK-02', '28.0'], ['CHAIR-03', '7.5']]);
const outbox = [];

// EXAMPLE-BEGIN register
function stock(args) {
  return Value.int(inventory.get(args.text(0)) ?? 0);
}

function reserve(args) {
  const sku = args.text(0), qty = args.nonNegInt(1);
  if ((inventory.get(sku) ?? 0) < qty) return Value.bool(false);
  inventory.set(sku, inventory.get(sku) - qty);
  return Value.bool(true);
}

function weight(args) {
  return Value.text(weights.get(args.text(0)) ?? '0');
}

function notify(args) {
  outbox.push(`${args.text(0)}: ${args.text(1)}`);
  return Value.bool(true);
}

registerFunction('STOCK', 1, 1, stock);
registerFunction('RESERVE', 2, 2, reserve);
registerFunction('WEIGHT', 1, 1, weight);
registerFunction('NOTIFY', 2, 2, notify);
// EXAMPLE-END register

// EXAMPLE-BEGIN run
const source = readFileSync(new URL('fulfil.sel', import.meta.url), 'utf8');
const fulfil = compile(source);             // after registering: names resolve now

console.log('1. the script reads', fulfil.dependencies().join(', '));
console.log('2. orders');
const orders = [
  { ORDER: 'A-1', COUNTRY: 'PL', ITEMS: [{ sku: 'LAMP-01', qty: '2' },
                                         { sku: 'CHAIR-03', qty: '1' }] },
  { ORDER: 'A-2', COUNTRY: 'DE', ITEMS: [{ sku: 'DESK-02', qty: '1' },
                                         { sku: 'CHAIR-03', qty: '2' }] },
  { ORDER: 'A-3', COUNTRY: 'PL', ITEMS: [{ sku: 'LAMP-01', qty: '3' }] },
  { ORDER: 'A-4', COUNTRY: 'PL', ITEMS: [{ sku: 'LAMP-01', qty: 'two' }] },
];
for (const order of orders) {
  let decision;
  try {
    decision = fulfil.run(Value.fromNative(order)).asText();
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    decision = `${e.code} at ${e.line}:${e.col}`;
  }
  console.log(`   ${order.ORDER}  ${decision}`);
}
// EXAMPLE-END run

console.log('3. outbox');
for (const message of outbox) console.log('  ', message);
console.log('4. stock left');
for (const sku of [...inventory.keys()].sort()) {
  console.log(`   ${sku.padEnd(9)} ${inventory.get(sku)}`);
}
