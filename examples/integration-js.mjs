// A realistic frontend integration: the same rule table the backend runs,
// compiled once, plus dependency-driven re-validation.
//
//   node examples/integration-js.mjs

import { compile, Value, SelError } from '../js/src/sel.mjs';

// Compile once, run per keystroke. A Program is immutable and reusable.
class RuleSet {
  constructor(sources) {
    this.rules = new Map();
    for (const [field, source] of Object.entries(sources)) {
      // A syntax error here is a build problem, not a user problem.
      this.rules.set(field, compile(source));
    }
  }

  // Runs every rule against one payload. A rule yields "" when the field is
  // fine, or a message when it is not.
  validate(payload, only = null) {
    const messages = {};
    for (const [field, program] of this.rules) {
      if (only && !only.includes(field)) continue;
      // Each rule gets its own context: rules must not see each other's
      // intermediate variables.
      const context = Value.fromNative(payload);
      let result;
      try {
        result = program.run(context).asText();
      } catch (e) {
        result = RuleSet.present(field, e);
      }
      if (result !== '') messages[field] = result;
    }
    return messages;
  }

  // E_ABORT is the author saying "tell the user this". Every other code means
  // the rule itself is broken — report it, do not show it.
  static present(field, e) {
    if (!(e instanceof SelError)) throw e;
    if (e.code === 'E_ABORT') return e.message;
    console.error(`SEL rule for ${field} failed: ${e}`);
    return 'could not be validated';
  }

  // Which rules read a given field, so an input listener knows the minimum set
  // to re-run. This is what dependencies() is for.
  watchMap() {
    const map = {};
    for (const [field, program] of this.rules) {
      for (const input of program.dependencies()) {
        (map[input] ||= []).push(field);
      }
    }
    return map;
  }
}

// --- the rules, identical to examples/integration-php.php --------------------

const rules = new RuleSet({
  email: `
    COND(TRIM(EMAIL) $== "",                            "Email is required.",
         NOT RMATCH('^[^@ ]+@[^@ .]+\\.[^@ ]+$', EMAIL), "That does not look like an email address.",
         LEN(EMAIL) > 254,                              "Email is too long.",
                                                        "")`,

  postcode: `
    COND(TRIM(POSTCODE) $== "",                 "Postcode is required.",
         NOT RMATCH('^\\d{2}-\\d{3}$', POSTCODE), "Use the format 12-345.",
                                                "")`,

  order: `
    TOTAL = SUM(ITEMS, _["QTY"] * _["PRICE"]);
    COND(COUNT(ITEMS) == 0,          "Your basket is empty.",
         ANY(ITEMS, _["QTY"] <= 0),  "Every line needs a quantity.",
         TOTAL > CREDIT_LIMIT,       "Total {TOTAL} exceeds your limit of {CREDIT_LIMIT}.",
                                     "")`,
});

// --- a form ------------------------------------------------------------------

const payload = {
  EMAIL: 'anna@example',
  POSTCODE: '31-874',
  CREDIT_LIMIT: '50.00',
  ITEMS: [
    { SKU: 'AB-1234', QTY: '3', PRICE: '19.99' },
    { SKU: 'CD-5678', QTY: '0', PRICE: '5.00' },
  ],
};

console.log('messages:');
for (const [field, message] of Object.entries(rules.validate(payload))) {
  console.log(`  ${field}: ${message}`);
}

payload.EMAIL = 'anna@example.com';
payload.ITEMS[1].QTY = '1';
console.log('after fixing email and quantity:');
const messages = rules.validate(payload);
console.log(Object.keys(messages).length ? `  ${JSON.stringify(messages)}` : '  (none)');

const watch = rules.watchMap();
console.log('watch map (field => rules to re-run):');
for (const input of Object.keys(watch).sort()) {
  console.log(`  ${input.padEnd(13)} ${watch[input].join(', ')}`);
}

// Only the affected rules re-run when one input changes.
console.log('EMAIL changed, so re-running:', watch.EMAIL.join(', '));
console.log('  ', JSON.stringify(rules.validate(payload, watch.EMAIL)));
