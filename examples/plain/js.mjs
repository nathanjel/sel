// Plain usage — calling SEL from JS.
//
//   node examples/plain/js.mjs
//
// The four files beside this one do the same thing through their own host API
// and print byte-identical output; tools/check-examples.sh diffs them. That is
// the point of the example as much as the code is: the differences you see
// between these files are the languages', never SEL's.

import { compile, evaluate, Value, SelError } from '../../js/src/sel.mjs';

// 1 — evaluate something ------------------------------------------------------

console.log('1. one-off');
console.log('   2.50 + 2.50 =>', evaluate('2.50 + 2.50').asText());

// 2 — compile once, run per keystroke -----------------------------------------
// Parsing is cheap but not free, and a Program is immutable and reusable. In a
// form you compile each rule once and keep it.

console.log('2. compile once, run many');
const rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');
for (const row of [{ QTY: '3', PRICE: '19.99' }, { QTY: '1', PRICE: '5.00' }]) {
  const ctx = Value.fromNative({ ...row, LIMIT: '50.00' });
  console.log(`   QTY=${row.QTY} PRICE=${row.PRICE} =>`, rule.run(ctx).asText());
}

// 3 — building a context ------------------------------------------------------
// Pass money as *strings*. A JS number is a double and has already lost the
// exactness SEL exists to preserve.

console.log('3. structured context');
const order = Value.fromNative({
  CUSTOMER: 'Zażółć',
  ITEMS: [                                        // an array is a 1-based list
    { SKU: 'AB-1234', QTY: '3', PRICE: '19.99' },
    { SKU: 'CD-5678', QTY: '1', PRICE: '5.01' },
  ],
});
console.log('   first SKU =>', compile('ITEMS[1]["SKU"]').run(order).asText());
console.log('   total     =>', compile('SUM(ITEMS, _["QTY"] * _["PRICE"])').run(order).asText());
console.log('   0.10+0.20 =>', evaluate('0.10 + 0.20').asText());

// 4 — reading results back ----------------------------------------------------
// A result is a Value: a scalar, children, both or neither.

console.log('4. reading results');
const v = evaluate('SPLIT("a,b,c", ",")');
console.log('   size   =>', v.size());
console.log('   keys   =>', v.keys().join(','));
console.log('   [2]    =>', v.get('2').asText());
console.log('   scalar =>', v.asText());            // scalar context: first child
// A host bool prints differently in all five languages (true/1/True/T), and
// this file's output has to be byte-identical to its four siblings, so say it
// in SEL's own spelling rather than the host's.
console.log('   bool   =>', evaluate('1 < 2').asBool() ? 'TRUE' : 'FALSE');

// 5 — the context is mutated, so rules hand values back -----------------------

console.log('5. variables the rule set');
const ctx = Value.fromNative({ QTY: '3', PRICE: '19.99' });
compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT').run(ctx);
for (const name of ['NET', 'VAT', 'GROSS']) {
  console.log(`   ${name.padEnd(5)} =>`, ctx.get(name).asText());
}

// 6 — errors ------------------------------------------------------------------
// Every failure carries a stable code and the position of the node that
// actually failed. Assert on the code, never on the message.

console.log('6. errors');
for (const src of ['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")']) {
  try {
    evaluate(src);
    console.log(`   ${src.padEnd(17)} => no error`);
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`   ${src.padEnd(17)} => ${e.code} at ${e.line}:${e.col}`);
  }
}

// 7 — which fields does this rule read? ---------------------------------------
// Found statically, without running it. Wire these to your input listeners and
// re-validation is free.

console.log('7. dependencies');
console.log('  ', compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""')
  .dependencies().join(' '));
