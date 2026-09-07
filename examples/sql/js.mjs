// SQL-aimed usage — pushing a rule down to the database, from JS.
//
//   node examples/sql/js.mjs
//
// The same rule that validates one order in the application can filter a
// million of them in the database. What makes that safe is that the translation
// refuses rather than guesses: if SQL cannot be made to mean what SEL means, no
// SQL is emitted and the rule stays where it already worked.
//
// The four files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.

import { compile } from '../../js/src/sel.mjs';
import { Binding, Sql, SqlError } from '../../js/src/sql/index.mjs';

// 1 — a rule, and what the database should call its inputs --------------------
// dependencies() says exactly what has to be bound. A name the program reads
// and the bindings do not describe is a refusal, not a guess.

console.log('1. a rule pushed down');
const rule = compile('TOTAL > 100.00 AND STATUS $== "open"');
console.log('   needs        =>', rule.dependencies().join(' '));

const bindings = {
  TOTAL:  Binding.column('total', 'o', 'NUM'),
  STATUS: Binding.column('status', 'o', 'TEXT'),
};
const frag = Sql.translate(rule, 'mariadb', bindings);
console.log('   sql          =>', frag.asCondition());

// 2 — the same rule as a prepared statement -------------------------------------
// Inline is for reading and for a query you build once. `params` is what you
// hand a driver: the literals become placeholders and bindings() gives the
// values in the order the placeholders appear in the output.

console.log('2. as parameters');
console.log('   sql          =>', frag.asCondition('params'));
console.log('   values       =>', frag.bindings().map((v) => v.dump()).join(', '));

// 3 — one rule, every dialect ----------------------------------------------------
// The differences below are the databases', not the rule's. Nothing in the
// program changed.

console.log('3. every dialect');
for (const dialect of Sql.dialects()) {
  console.log(`   ${dialect.padEnd(12)} =>`, Sql.translate(rule, dialect, bindings).asCondition());
}

// 4 — a rule over a related table -------------------------------------------------
// An aggregate over a relation becomes EXISTS / NOT EXISTS with a correlation,
// which is the shape a database can actually use an index for.

console.log('4. over a relation');
const lines = compile('ALL(ITEMS, I, I["QTY"] > 0)');
console.log('   sql          =>', Sql.translate(lines, 'mariadb', {
  ITEMS: Binding.relation('order_items', 'oi',
    { QTY: Binding.column('qty', null, 'NUM') },
    null, '`oi`.`order_id` = `o`.`id`'),
}).asCondition());

// 5 — refusal is an ordinary answer ------------------------------------------------
// tryTranslate returns null so the caller can fall back to the evaluator without
// a try/catch. translate() throws the same refusal with the reason written out,
// which is what you want in a build-time audit of a rule set.

console.log('5. refusal');
const unbound = compile('MYSTERY > 1');
console.log('   tryTranslate =>', Sql.tryTranslate(unbound, 'mariadb', bindings) === null
  ? 'null — evaluate it in the host instead' : 'translated');
try {
  Sql.translate(unbound, 'mariadb', bindings);
} catch (e) {
  if (!(e instanceof SqlError)) throw e;
  console.log('   translate    =>', e.code);
}

// 6 — what a fragment knows about itself ---------------------------------------------
// A caveat is the map saying "this dialect's answer may differ from SEL's here".
// An empty list is the layer promising it does not.

console.log('6. the fragment');
console.log('   kind         =>', frag.kind);
console.log('   dialect      =>', frag.dialect);
console.log('   exact        =>', frag.caveats.length === 0 ? 'TRUE' : 'FALSE');
