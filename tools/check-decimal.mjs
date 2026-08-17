#!/usr/bin/env node
// Checks the JS decimal core against the Python oracle.
//   node tools/check-decimal.mjs oracle.txt

import { readFileSync } from 'node:fs';
import * as D from '../js/src/decimal.mjs';

const cases = readFileSync(process.argv[2], 'utf8')
  .split('\n')
  .filter((line) => line !== '')
  .map((line) => {
    const [op, a, b, want] = line.split('|');
    return { op, a, b, want };
  });
const failures = [];
// Counted separately from the displayed list: capping both would report "20
// mismatches" whether 20 or 20000 cases were wrong, which is exactly the moment
// the number matters.
let mismatches = 0;

for (const c of cases) {
  const a = D.parse(c.a);
  const b = D.parse(c.b);
  let got;
  try {
    switch (c.op) {
      case '+': got = D.format(D.add(a, b)); break;
      case '-': got = D.format(D.sub(a, b)); break;
      case '*': got = D.format(D.mul(a, b)); break;
      case '/': got = D.format(D.div(a, b)); break;
      case '%': got = D.format(D.mod(a, b)); break;
      case 'cmp': got = String(D.cmp(a, b)); break;
      case 'round': got = D.format(D.round(a, Number(c.b))); break;
      case 'floor': got = D.format(D.floor(a)); break;
      case 'ceil': got = D.format(D.ceil(a)); break;
      case 'trunc': got = D.format(D.trunc(a)); break;
      default: throw new Error(`unknown op ${c.op}`);
    }
  } catch (e) {
    got = `THREW ${e.code || e.message}`;
  }
  if (got !== c.want) {
    mismatches++;
    if (failures.length < 20) {
      failures.push(`${c.a} ${c.op} ${c.b} => ${got}, oracle says ${c.want}`);
    }
  }
}

console.log(`js:  ${cases.length} cases, ${mismatches} mismatches`);
failures.forEach((f) => console.log(`  ${f}`));
if (mismatches > failures.length) console.log(`  ... and ${mismatches - failures.length} more`);
process.exitCode = mismatches ? 1 : 0;
