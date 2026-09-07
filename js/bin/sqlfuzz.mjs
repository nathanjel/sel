#!/usr/bin/env node
// Translates a corpus of SEL programs and prints one canonical line each, so
// that every host WITH a translator can be compared with a plain diff.
//
//   node js/bin/sqlfuzz.mjs corpus.selc [dialect]
//
// This is the check docs/SQL-TRANSLATION.md §14 M7 asked for and nothing built.
// The oracle lane answers "does the emitted SQL MEAN what SEL means?" and needs
// a database; this one answers "do the hosts emit the SAME thing?" and needs
// nothing. Without it, `tools/check.sh`'s SQL fuzz step was a no-op on any
// machine with no DSN set — which is every machine by default — so three
// translators agreeing was asserted only over the 377 hand-written cases.
//
// The corpus format and the one-line-per-program protocol are specified in
// tools/README.md, and this reader is the same five lines as the others.

import { readFileSync } from 'node:fs';
import { compile, SelError } from '../src/sel.mjs';
import { Sql, SqlError } from '../src/sql/index.mjs';

const [path, dialect = 'mariadb'] = process.argv.slice(2);

function readCorpus(text) {
  const records = [];
  let cur = null;
  for (const line of text.split('\n')) {
    if (line.startsWith('### ')) { cur = []; records.push(cur); continue; }
    if (cur) cur.push(line);
  }
  return records.map((lines) => lines.join('\n').replace(/\n$/, ''));
}

const lines = [];
for (const src of readCorpus(readFileSync(path, 'utf8'))) {
  let program;
  try {
    program = compile(src);
  } catch (e) {
    // Does not compile. tools/fuzz.sh already holds every host to the same
    // answer here, so this lane says only that it got that far.
    lines.push(e instanceof SelError ? '-' : `!HOST ${e.constructor.name}`);
    continue;
  }
  try {
    // `asValue` rather than `asCondition`: the corpus is arbitrary expressions,
    // most of which are not BOOL, and refusing them all for that would compare
    // the same refusal a thousand times.
    //
    // Three renderings, not one. Inline alone would have missed a whole class:
    // whether a slot is a literal or a placeholder is a `params`-mode decision,
    // and the bound values are a third thing again -- a host that emits the same
    // string while binding different values is exactly what this lane is for.
    const f = Sql.translate(program, dialect);
    lines.push([f.asValue(), f.asValue('params'),
                f.bindings().map((v) => v.dump()).join(',')].join(' | '));
  } catch (e) {
    if (e instanceof SqlError) lines.push(`!${e.code}@${e.line}:${e.col}`);
    // A SEL error raised DURING translation is still a translator answer -- the
    // layer evaluates constant subtrees -- and must agree like any other.
    else if (e instanceof SelError) lines.push(`!SEL ${e.code}@${e.line}:${e.col}`);
    else lines.push(`!HOST ${e.constructor.name}: ${e.message}`);
  }
}
process.stdout.write(lines.map((l) => l.replace(/\n/g, '\\n')).join('\n') + '\n');
