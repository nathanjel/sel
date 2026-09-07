// Complex usage — the language's reach, from JS.
//
//   node examples/complex/js.mjs
//
// examples/plain/ is the API. This is the language: aggregates, named binders,
// text and regex, structured results, and a rule that refuses. The four files
// beside this one print byte-identical output; tools/check-examples.sh diffs
// them.

import { compile, evaluate, Value, SelError } from '../../js/src/sel.mjs';

const order = Value.fromNative({
  CUSTOMER: 'Zażółć Gęślą',
  POSTCODE: '31-874',
  CREDIT_LIMIT: '100.00',
  ITEMS: [
    { SKU: 'AB-1234', QTY: '3', PRICE: '19.99' },
    { SKU: 'CD-5678', QTY: '1', PRICE: '5.01' },
    { SKU: 'EF-9012', QTY: '2', PRICE: '0.50' },
  ],
});
const ask = (src) => compile(src).run(order).asText();

// 1 — a rule set, not an expression -------------------------------------------
// `;` separates statements and the last one is the answer. Intermediate names
// are ordinary variables, so a long rule reads top to bottom.

console.log('1. a rule set');
console.log('  ', compile([
  'NET   = SUM(ITEMS, _["QTY"] * _["PRICE"])',
  'VAT   = ROUND(NET * 0.23, 2)',
  'GROSS = NET + VAT',
  'IF(GROSS > CREDIT_LIMIT, "refer: " & GROSS, "accept: " & GROSS)',
].join('; ')).run(order).asText());

// 2 — aggregates ---------------------------------------------------------------
// No loops. A body expression is evaluated once per element with `_` bound to
// the element and `_K` to its key.

console.log('2. aggregates');
console.log('   lines        =>', ask('COUNT(ITEMS)'));
console.log('   net          =>', ask('SUM(ITEMS, _["QTY"] * _["PRICE"])'));
console.log('   all in stock =>', ask('IF(ALL(ITEMS, _["QTY"] > 0), "TRUE", "FALSE")'));
console.log('   any > 10     =>', ask('IF(ANY(ITEMS, _["PRICE"] > 10.00), "TRUE", "FALSE")'));
console.log('   dearest      =>', ask('MAX(MAP(ITEMS, _["PRICE"]))'));

// 3 — MAP renumbers, FILTER keeps the keys --------------------------------------
// A filtered list stays addressable the way its source was, which is why the
// dump below has holes in it. That is the contract, not an accident.

console.log('3. map and filter');
console.log('   skus         =>', ask('JOIN(MAP(ITEMS, _["SKU"]), ", ")'));
console.log('   bulk keys    =>', compile('FILTER(ITEMS, _["QTY"] > 1)').run(order).keys().join(','));
console.log('   keyed        =>', compile('MAP(FILTER(ITEMS, _["QTY"] > 1), _K & ":" & _["SKU"])')
  .run(order).dump());

// 4 — naming the binder, for nesting ---------------------------------------------
// `_` is the innermost element. The three-argument form names it instead, which
// is the only way an outer element stays reachable from an inner body.

console.log('4. named binders');
console.log('  ', evaluate(
  'R[1] = (1, 2); R[2] = (3, 4); IF(ALL(R, ROW, ALL(ROW, _ > 0)), "all positive", "no")')
  .asText());

// 5 — text and regex ---------------------------------------------------------------
// Patterns are a portable subset, checked at compile time: a regex that would
// mean different things on different hosts is refused rather than guessed at.

console.log('5. text and regex');
// UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
// ę below come back unchanged. That is not a shortcoming, it is the only way
// five hosts can agree. Measured on a sharp s: JS's toUpperCase and Python's
// str.upper both answer SS, PHP's strtoupper answers ß, and C's toupper cannot
// see it at all. SEL answers ß on all five, because it never asks the host.
console.log('   upper        =>', ask('UPPER(CUSTOMER)'));
console.log('   initials     =>', ask('JOIN(MAP(SPLIT(CUSTOMER, " "), LEFT(_, 1)), ".")'));
console.log('   postcode     =>', ask('IF(RMATCH(\'^[0-9]{2}-[0-9]{3}$\', POSTCODE), "ok", "bad")'));
// RGROUPS puts the WHOLE match at "1", so the first capture is "2".
console.log('   area         =>', ask('RGROUPS(\'^([0-9]{2})-\', POSTCODE)["2"]'));
console.log('   padded       =>', ask('PADL(COUNT(ITEMS), 3, "0")'));

// 6 — asking whether a key is there --------------------------------------------------

console.log('6. presence');
console.log('   HAS SKU      =>', ask('IF(HAS(ITEMS[1], "SKU"), "TRUE", "FALSE")'));
console.log('   HAS NOTE     =>', ask('IF(HAS(ITEMS[1], "NOTE"), "TRUE", "FALSE")'));
console.log('   missing      =>', (() => {
  try { return ask('ITEMS[1]["NOTE"]'); } catch (e) { return `${e.code} at ${e.line}:${e.col}`; }
})());

// 7 — a rule that refuses -------------------------------------------------------------
// ABORT is how a rule says "this is not valid", as distinct from "this could not
// be computed". Both arrive as the same error type, told apart by the code.

console.log('7. business refusal');
for (const [label, src] of [
  ['under limit', 'IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > CREDIT_LIMIT, ABORT("over credit limit"), "ok")'],
  ['over limit', 'IF(SUM(ITEMS, _["QTY"] * _["PRICE"]) > 50.00, ABORT("over credit limit"), "ok")'],
  ['blank name', 'IF(TRIM("   ") $== "", ABORT("customer required"), "ok")'],
]) {
  try {
    console.log(`   ${label.padEnd(11)} =>`, ask(src));
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`   ${label.padEnd(11)} => ${e.code}: ${e.message}`);
  }
}

// 8 — where a failure actually happened -------------------------------------------------
// The position is the node that failed, not the statement or the call that
// contains it. That is what makes a long rule debuggable.

console.log('8. error positions');
for (const src of ['1 + ROUND(2 + "x", 2)', 'SUM(ITEMS, _["QTY"] * _["NOPE"])']) {
  try {
    compile(src).run(order);
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`   ${e.code} at ${e.line}:${e.col}  ${src}`);
  }
}
