// Form validation -- one rule set, the same verdicts in every host, from JavaScript.
//
//   node examples/validation/js.mjs
//
// A checkout form's rules, one per field, written in SEL. The server compiles
// them once at start-up, so a rule that does not parse fails the deployment
// rather than a customer. Each rule answers "" when the field is fine and
// ABORT("message") when it is not, so there are two kinds of failure and they
// are told apart by code: E_ABORT is a message for the user, anything else means
// the rule itself is broken and the user should never see it. dependencies()
// tells a browser which rules to re-run when a field changes -- and the browser
// runs the very same rule text, in JavaScript.
//
// The four files beside this one print byte-identical output.

import { compile, SelError, Value } from '../../js/src/sel.mjs';

// EXAMPLE-BEGIN rules
const RULES = {
  name:     'IF(IS_BLANK(NAME), ABORT("Please tell us your name"), "")',
  email:    "IF(RMATCH('^[^@ ]+@[^@ ]+\\.[a-z]{2,}$', TRIM(EMAIL), \"i\"), \"\","
            + ' ABORT("{EMAIL} does not look like an e-mail address"))',
  postcode: 'COND(COUNTRY $== "PL" AND NOT RMATCH(\'^\\d{2}-\\d{3}$\', POSTCODE),'
            + '       ABORT("Polish postcodes look like 00-000"),'
            + '     COUNTRY $== "DE" AND NOT RMATCH(\'^\\d{5}$\', POSTCODE),'
            + '       ABORT("German postcodes have five digits"),'
            + '     "")',
  quantity: 'IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,'
            + ' ABORT("Choose between 1 and {STOCK}"), "")',
  total:    'TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);'
            + ' IF(TOTAL > CREDIT_LIMIT, ABORT("{TOTAL} is over your limit of {CREDIT_LIMIT}"), "")',
};
// EXAMPLE-END rules

// 1 - compile once, at start-up ----------------------------------------------------

// EXAMPLE-BEGIN compile
const compiled = Object.fromEntries(
  Object.entries(RULES).map(([field, source]) => [field, compile(source)]));
// EXAMPLE-END compile
console.log('1. the rule set');
for (const [field, rule] of Object.entries(compiled)) {
  console.log(`   ${field.padEnd(9)} reads`, rule.dependencies().join(' '));
}

// 2 - what to re-check when a field changes ----------------------------------------

console.log('2. re-check on change');
const watch = new Map();
for (const [field, rule] of Object.entries(compiled)) {
  for (const name of rule.dependencies()) {
    if (!watch.has(name)) watch.set(name, []);
    watch.get(name).push(field);
  }
}
for (const name of [...watch.keys()].sort()) {
  console.log(`   ${name.padEnd(13)}`, watch.get(name).join(', '));
}

// 3 - validating submissions -----------------------------------------------------------

// EXAMPLE-BEGIN validate
function validate(form) {
  const problems = {};
  for (const [field, rule] of Object.entries(compiled)) {
    let verdict;
    try {
      verdict = rule.run(Value.fromNative(form)).asText();
    } catch (e) {
      if (!(e instanceof SelError)) throw e;
      // E_ABORT is the rule speaking to the user; anything else is a
      // broken rule or data it cannot read -- log it, show a generic line.
      verdict = e.code === 'E_ABORT' ? e.message : `could not be checked (${e.code})`;
    }
    if (verdict !== '') problems[field] = verdict;
  }
  return problems;
}
// EXAMPLE-END validate

const SUBMISSIONS = [
  { NAME: 'Anna Nowak', EMAIL: 'anna@example.pl', COUNTRY: 'PL', POSTCODE: '31-874',
    QTY: '2', STOCK: '5', PRICE: '19.99', DISCOUNT: '0.10', CREDIT_LIMIT: '100.00' },
  { NAME: '   ', EMAIL: 'bruno(at)example.de', COUNTRY: 'DE', POSTCODE: '1011',
    QTY: '9', STOCK: '5', PRICE: '19.99', DISCOUNT: '0', CREDIT_LIMIT: '100.00' },
  { NAME: 'Chloé', EMAIL: 'CHLOE@EXAMPLE.FR ', COUNTRY: 'FR', POSTCODE: '69002',
    QTY: '4', STOCK: '5', PRICE: '29.99', DISCOUNT: '0.05', CREDIT_LIMIT: '100.00' },
  { NAME: 'Dawid', EMAIL: 'dawid@example.pl', COUNTRY: 'PL', POSTCODE: '00-950',
    QTY: '1', STOCK: '5', PRICE: 'twenty', DISCOUNT: '0', CREDIT_LIMIT: '100.00' },
];

console.log('3. submissions');
SUBMISSIONS.forEach((form, i) => {
  const problems = validate(form);
  if (Object.keys(problems).length === 0) console.log(`   #${i + 1} accepted`);
  for (const [field, message] of Object.entries(problems)) {
    console.log(`   #${i + 1} ${field.padEnd(9)} ${message}`);
  }
});
