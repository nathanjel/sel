// Adding a SQL flavour — teaching the translator about your database, from JS.
//
//   node examples/dialect/js.mjs
//
// The shipped map covers four targets over two bases. A deployment is rarely
// exactly one of them: a driver wants numbered placeholders, a function is
// spelled differently, an extension is not installed. A dialect is registered
// rather than forked, so what you write is only the difference.
//
// The four files beside this one print byte-identical output;
// tools/check-examples.sh diffs them.

import { compile } from '../../js/src/sel.mjs';
import { Binding, Fragment, Sql, map } from '../../js/src/sql/index.mjs';

const rule = compile('NAME $== "ok" AND TOTAL > 10.00');
const bindings = {
  NAME:  Binding.column('name', 't', 'TEXT'),
  TOTAL: Binding.column('total', 't', 'NUM'),
};
const sqlIn = (dialect) => Sql.translate(rule, dialect, bindings).asCondition('params');

// 1 — what ships ---------------------------------------------------------------

console.log('1. what ships');
console.log('   targets      =>', Sql.dialects().join(' '));
console.log('   postgresql   =>', map.chain('postgresql').join(' -> '));

// 2 — a flavour of your own -------------------------------------------------------
// `extends` is the whole mechanism: the new dialect answers for what it declares
// and defers upward for everything else. Two-phase lookup -- the whole overlay
// chain, then the whole shipped chain -- so an override never half-applies.

console.log('2. a flavour of your own');
map.defineDialect('pg-libpq', {
  extends: 'postgresql',
  version: '15',
  target: true,                       // a base is not a target; this is a server
  lexical: { placeholder: '${n}' },   // libpq numbers its parameters
});
console.log('   targets      =>', Sql.dialects().join(' '));
console.log('   chain        =>', map.chain('pg-libpq').join(' -> '));
console.log('   base         =>', sqlIn('postgresql'));
console.log('   pg-libpq     =>', sqlIn('pg-libpq'));

// 3 — spelling one function differently ---------------------------------------------
// {*} is every argument; {0}, {1} pick them out. Note the slots are ZERO-based
// while every position SEL reports is one-based -- these are template holes, not
// SEL positions. The entry also says what it returns, because the translator
// infers kinds and will not guess.

console.log('3. one function, respelled');
map.define('pg-libpq', 'funcs', 'UPPER', { tpl: 'UPPER({0} COLLATE "C")', ret: 'TEXT' });
console.log('   upper        =>',
  Sql.translate(compile('UPPER(NAME)'), 'pg-libpq', bindings).asValue());

// 4 — withdrawing what a deployment does not have ------------------------------------
// A null entry withdraws it. This is not the same as leaving it unmapped: it is
// the map saying "not here", and the rule is refused rather than emitted against
// a function the server does not have.

console.log('4. withdrawing an entry');
map.define('pg-libpq', 'funcs', 'RMATCH', null);
const re = compile('RMATCH(\'^a\', NAME)');
console.log('   postgresql   =>', Sql.tryTranslate(re, 'postgresql', bindings) === null
  ? 'refused' : 'translated');
console.log('   pg-libpq     =>', Sql.tryTranslate(re, 'pg-libpq', bindings) === null
  ? 'refused' : 'translated');

// 5 — a builder, for what a template cannot say -----------------------------------------
// The escape hatch. It receives the emitter and the already-rendered arguments,
// and returns a fragment, so it can do what no string with holes in it can.
//
// Splice the argument's `parts` rather than its rendered SQL. A part list is
// strings alternating with parameter slots, so splicing keeps a bound value
// bound; flattening it to a string first would inline whatever the argument
// carried and quietly turn a prepared statement back into concatenation.

console.log('5. a builder');
map.defineBuilder('pg-libpq', 'funcs', 'LEN', (emit, args) =>
  new Fragment(['length(', ...args[0].parts, ')'], 'NUM', emit.dialect()));
console.log('   len          =>',
  Sql.translate(compile('LEN(NAME)'), 'pg-libpq', bindings).asValue());

// 6 — putting it back ---------------------------------------------------------------------
// reset() drops every registration and leaves the shipped map. Worth knowing in
// a test suite: a registration that leaks into the next test is a test that
// passes for the wrong reason.

console.log('6. reset');
map.reset();
console.log('   targets      =>', Sql.dialects().join(' '));
console.log('   pg-libpq     =>', map.exists('pg-libpq') ? 'still there' : 'gone');
