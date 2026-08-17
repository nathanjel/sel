// Calling SEL from JS. Run: node examples/host-js.mjs

import { compile, evaluate, Value, SelError } from '../js/src/sel.mjs';

// 1 — evaluate something -----------------------------------------------------

console.log('1. one-off');
console.log('  ', evaluate('2.50 + 2.50').asText());

// 2 — compile once, run per keystroke ----------------------------------------
// Parsing is cheap but not free, and a Program is immutable and reusable. In a
// form you compile each rule once and keep it.

console.log('2. compile once, run many');
const rule = compile('IF(QTY * PRICE > LIMIT, "over budget", "ok")');

for (const row of [{ QTY: '3', PRICE: '19.99' }, { QTY: '1', PRICE: '5.00' }]) {
  const ctx = Value.fromNative({ ...row, LIMIT: '50.00' });
  console.log('  ', rule.run(ctx).asText());
}

// 3 — building a context ------------------------------------------------------
// fromNative takes scalars, arrays and nested objects. Pass money as *strings*:
// a JS number is a double and has already lost the exactness SEL preserves.

console.log('3. structured context');
const order = Value.fromNative({
  CUSTOMER: 'Zażółć',
  ITEMS: [                                        // an array is a 1-based list
    { SKU: 'AB-1234', QTY: '3', PRICE: '19.99' },
    { SKU: 'CD-5678', QTY: '1', PRICE: '5.01' },
  ],
});
console.log('   first SKU:', compile('ITEMS[1]["SKU"]').run(order).asText());
console.log('   total:    ', compile('SUM(ITEMS, _["QTY"] * _["PRICE"])').run(order).asText());
console.log('   0.1+0.2:  ', evaluate('0.10 + 0.20').asText(), '  (JS says', 0.1 + 0.2, ')');

// 4 — reading results back ----------------------------------------------------
// A result is a Value. Use asText/asBool/asDecimal for scalars, or walk it.

console.log('4. reading results');
const v = evaluate('SPLIT("a,b,c", ",")');
console.log('   count:  ', v.size());
console.log('   keys:   ', v.keys().join(','));
console.log('   [2]:    ', v.get('2').asText());
console.log('   scalar: ', v.asText());                  // scalar context: first child
console.log('   native: ', JSON.stringify(v.toNative()));
console.log('   bool:   ', evaluate('1 < 2').asBool());

// 5 — the context is mutated, so rules can hand values back -------------------

console.log('5. reading variables the rule set');
const ctx = Value.fromNative({ QTY: '3', PRICE: '19.99' });
compile('NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT').run(ctx);
for (const name of ['NET', 'VAT', 'GROSS']) {
  console.log(`   ${name} =`, ctx.get(name).asText());
}

// 6 — errors ------------------------------------------------------------------
// Everything throws SelError with a stable code and the position of the node
// that actually failed. Assert on .code, never on the message.

console.log('6. errors');
for (const src of ['3 + "A"', 'NOSUCH(1)', 'IF(1, "a", "b")', 'ABORT("no stock")']) {
  try {
    evaluate(src);
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`   ${src.padEnd(18)} ${e.code} at ${e.line}:${e.col} — ${e.message}`);
  }
}

// 7 — which fields does this rule read? ---------------------------------------
// Static, without running it. Wire these to your input listeners and you have
// re-validation for free.

console.log('7. dependencies');
const p = compile('T = SUM(ITEMS, _["QTY"]); T > LIMIT AND CUSTOMER $!= ""');
console.log('  ', p.dependencies().join(' '));
