#!/usr/bin/env node
// Drives examples/order-validation.sel through the JS host API and prints a
// canonical report. examples/e2e.php does the same through the PHP host API,
// and tools/e2e.sh diffs the two.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { compile, Value, SelError } from '../js/src/sel.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(resolve(HERE, 'order-validation.sel'), 'utf8');
const program = compile(source);

// Prices are strings, not JS numbers: 0.1 + 0.2 must not become 0.30000000000004
// on one side of the wire and 0.30 on the other.
const SCENARIOS = {
  'valid order': {
    CUSTOMER: 'Zażółć Gęślą',
    POSTCODE: '31-874',
    CREDIT_LIMIT: '1000.00',
    ITEMS: [
      { SKU: 'AB-1234', QTY: '3', PRICE: '19.99' },
      { SKU: 'CD-5678', QTY: '1', PRICE: '5.01' },
    ],
  },
  'blank customer': {
    CUSTOMER: '   ', POSTCODE: '31-874', CREDIT_LIMIT: '1000.00',
    ITEMS: [{ SKU: 'AB-1234', QTY: '1', PRICE: '1.00' }],
  },
  'bad postcode': {
    CUSTOMER: 'Anna', POSTCODE: '318744', CREDIT_LIMIT: '1000.00',
    ITEMS: [{ SKU: 'AB-1234', QTY: '1', PRICE: '1.00' }],
  },
  'no lines': {
    CUSTOMER: 'Anna', POSTCODE: '31-874', CREDIT_LIMIT: '1000.00', ITEMS: [],
  },
  'zero quantity': {
    CUSTOMER: 'Anna', POSTCODE: '31-874', CREDIT_LIMIT: '1000.00',
    ITEMS: [
      { SKU: 'AB-1234', QTY: '1', PRICE: '1.00' },
      { SKU: 'CD-5678', QTY: '0', PRICE: '2.00' },
    ],
  },
  'malformed sku': {
    CUSTOMER: 'Anna', POSTCODE: '31-874', CREDIT_LIMIT: '1000.00',
    ITEMS: [{ SKU: 'oops', QTY: '1', PRICE: '1.00' }],
  },
  'over credit limit': {
    CUSTOMER: 'Anna', POSTCODE: '31-874', CREDIT_LIMIT: '10.00',
    ITEMS: [{ SKU: 'AB-1234', QTY: '3', PRICE: '19.99' }],
  },
  'exact-cent arithmetic': {
    CUSTOMER: 'Anna', POSTCODE: '31-874', CREDIT_LIMIT: '0.30',
    ITEMS: [
      { SKU: 'AB-1234', QTY: '1', PRICE: '0.10' },
      { SKU: 'CD-5678', QTY: '1', PRICE: '0.20' },
    ],
  },
};

const lines = [`dependencies: ${program.dependencies().join(' ')}`];
for (const [name, data] of Object.entries(SCENARIOS)) {
  let result;
  try {
    result = program.run(Value.fromNative(data)).dump();
  } catch (e) {
    result = e instanceof SelError ? `!${e.code}@${e.line}:${e.col}` : `!HOST ${e.message}`;
  }
  lines.push(`${name.padEnd(24)} ${result}`);
}
process.stdout.write(lines.join('\n') + '\n');
