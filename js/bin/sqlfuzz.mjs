#!/usr/bin/env node
// Translates a corpus of SEL programs and prints one canonical line each, so
// that every host WITH a translator can be compared with a plain diff.
//
//   node js/bin/sqlfuzz.mjs corpus.selc [dialect]
//
// This is the check docs/internals/sql-translation.md §14 M7 asked for and nothing built.
// The oracle lane answers "does the emitted SQL MEAN what SEL means?" and needs
// a database; this one answers "do the hosts emit the SAME thing?" and needs
// nothing. Without it, `tools/check.sh`'s SQL fuzz step was a no-op on any
// machine with no DSN set — which is every machine by default — so the
// translators agreeing was asserted only over the hand-written cases.
//
// The corpus format and the one-line-per-program protocol are specified in
// tools/README.md, and this reader is the same five lines as the others.

import { compile, SelError } from '../src/sel.mjs';
import { Binding, Sql, SqlError } from '../src/sql/index.mjs';
import { readTextOrExit } from './read-input.mjs';

// The relations the corpus's pipelines read (tools/gen-programs.mjs --sql), the
// same in every host's runner: two tables, a NUM join key, a TEXT field whose
// name both sides share.
// AMOUNT declares no table on purpose: a field without one renders bare on its
// own relation and must be qualified by the relation's alias once a join is
// present, and one host did not (SEL-0042). Every runner binds it this way, so
// the host-versus-host lane keeps watching that seam.
const bindings = {
  ORDERS: Binding.relation('orders', 'o', {
    ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
    AMOUNT: Binding.column('amount', null, 'NUM'), NAME: Binding.column('name', 'o', 'TEXT') }),
  CUSTOMERS: Binding.relation('customers', 'c', {
    ID: Binding.column('id', 'c', 'NUM'), NAME: Binding.column('name', 'c', 'TEXT') }),
};
const render = (f) => [f.asValue(), f.asValue('params'), f.bindings().map((v) => v.dump()).join(',')].join(' | ');
// Three lanes per program, `||`-separated: translate(), translateStatement()
// and planHybrid() (its classification, then its statement in params mode).
const attempt = (fn) => {
  try { return fn(); } catch (e) {
    if (e instanceof SqlError) return `!${e.code}@${e.line}:${e.col}`;
    if (e instanceof SelError) return `!SEL ${e.code}@${e.line}:${e.col}`;
    return `!HOST ${e.constructor.name}: ${e.message}`;
  }
};

// A third argument `statement` prints only translateStatement's inline SQL (or
// `!CODE@line:col`), one line per program: what php/bin/sqlo's cross-host
// statement oracle executes against a real server,
// so every host's translator -- not only PHP's -- is asked whether its SQL
// means what SEL means.
const [path, dialect = 'mariadb', mode = 'all'] = process.argv.slice(2);
if (path === undefined) {
  process.stderr.write('usage: sqlfuzz.mjs CORPUS [dialect] [statement]\n');
  process.exit(2);
}

function readCorpus(text) {
  const records = [];
  let cur = null;
  for (const line of text.split('\n')) {
    if (line.startsWith('### ')) { cur = []; records.push(cur); continue; }
    if (cur) cur.push(line);
  }
  return records.map((lines) => lines.join('\n').replace(/\n$/, ''));
}

// Exactly one trailing newline comes off each record (the corpus rule in
// CLAUDE.md); a CR anywhere is program text.
const corpus = readCorpus(readTextOrExit(path));
if (corpus.length === 0) {
  process.stderr.write(`no programs in ${path}\n`);
  process.exit(1);
}
const lines = [];
for (const src of corpus) {
  let program;
  try {
    program = compile(src);
  } catch (e) {
    // Does not compile. tools/fuzz.sh already holds every host to the same
    // answer here, so this lane says only that it got that far.
    lines.push(e instanceof SelError ? '-' : `!HOST ${e.constructor.name}`);
    continue;
  }
  if (mode === 'statement') {
    lines.push(attempt(() => Sql.translateStatement(program, dialect, bindings).asStatement()));
    continue;
  }
  // `asValue` rather than `asCondition`: the corpus is arbitrary expressions,
  // most of which are not BOOL, and refusing them all for that would compare
  // the same refusal a thousand times.
  //
  // Three renderings, not one. Inline alone would have missed a whole class:
  // whether a slot is a literal or a placeholder is a `params`-mode decision,
  // and the bound values are a third thing again -- a host that emits the same
  // string while binding different values is exactly what this lane is for.
  // Each is its own attempt(), which turns every outcome into a line.
  lines.push([
    attempt(() => render(Sql.translate(program, dialect, bindings))),
    attempt(() => render(Sql.translateStatement(program, dialect, bindings))),
    attempt(() => {
      const plan = Sql.planHybrid(program, dialect, bindings);
      const kind = plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid';
      return plan.sqlStatement ? `${kind} ${plan.sqlStatement.asStatement('params')}` : kind;
    }),
  ].join(' || '));
}
process.stdout.write(lines.map((l) => l.replace(/\n/g, '\\n')).join('\n') + '\n');
