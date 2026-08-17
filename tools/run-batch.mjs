#!/usr/bin/env node
// Runs a corpus of SEL programs and prints one canonical line each, so every
// implementation's output can be compared with a plain diff. Both the corpus
// format and the line format are specified in tools/README.md.
//
//   node tools/run-batch.mjs [--show] corpus.selc

import { readFileSync } from 'node:fs';
// SEL_JS_ENTRY aims this runner at a different build of the implementation —
// tools/impls.sh sets it to dist/sel.mjs so the bundle is held to the same
// suite as the source. Dynamic import because the specifier is not a constant.
const { compile, Value, SelError } =
  await import(process.env.SEL_JS_ENTRY ?? '../js/src/sel.mjs');

const args = process.argv.slice(2);
const show = args.includes('--show');
const path = args.filter((a) => a !== '--show')[0];

// A line beginning `### ` starts a record; everything after it is source until
// the next marker. Five lines, in any language — that is the whole point.
function readCorpus(text) {
  const records = [];
  let cur = null;
  for (const line of text.split('\n')) {
    if (line.startsWith('### ')) { cur = []; records.push(cur); continue; }
    if (cur) cur.push(line);
  }
  return records.map((lines) => lines.join('\n').replace(/\n$/, ''));
}

// The rendering bin/sel uses, so a documentation example can be pasted into the
// CLI and produce exactly what the documentation claims.
function render(v) {
  if (v.size === 0) {
    if (v.kind === 'TEXT') return v.scalar;
    if (v.kind === 'BOOL') return v.scalar ? 'TRUE' : 'FALSE';
    if (v.kind === 'BIN') return `bin:${v.dump().slice(1)}`;
  }
  return v.dump();
}

const lines = [];
for (const src of readCorpus(readFileSync(path, 'utf8'))) {
  try {
    const v = compile(src).run(Value.none());
    lines.push(show ? render(v) : v.dump());
  } catch (e) {
    if (e instanceof SelError) {
      lines.push(show ? `!${e.code}` : `!${e.code}@${e.line}:${e.col}`);
    } else {
      lines.push(`!HOST ${e.constructor.name}: ${e.message}`);
    }
  }
}
// One line per program is the protocol; a value containing a newline must not be
// allowed to desynchronise the comparison.
process.stdout.write(lines.map((l) => l.replace(/\n/g, '\\n')).join('\n') + '\n');
