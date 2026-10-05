#!/usr/bin/env node
// Runs a corpus of SEL programs and prints one canonical line each, so every
// implementation's output can be compared with a plain diff. Both the corpus
// format and the line format are specified in tools/README.md.
//
//   node tools/run-batch.mjs [--show] corpus.selc

import { readFileSync } from 'node:fs';
// The corpus reader and the rendering bin/sel uses (--show), shared with the
// CLI and with js/bin/sqlfuzz.mjs rather than copied: duck-typed, so they serve
// a bundle build as well as the source tree.
import { readCorpus } from '../js/bin/read-input.mjs';
import { show as render } from '../js/bin/show.mjs';
// SEL_JS_ENTRY aims this runner at a different build of the implementation —
// tools/impls.sh sets it to dist/sel.mjs so the bundle is held to the same
// suite as the source. Dynamic import because the specifier is not a constant.
const { compile, Value, SelError } =
  await import(process.env.SEL_JS_ENTRY ?? '../js/src/sel.mjs');

// The runner contract (tools/README.md): a path that cannot be read, or a
// corpus with no program in it, is a one-line refusal and a non-zero exit --
// never a stack trace, and never a successful run that compared nothing.
const args = process.argv.slice(2);
const show = args.includes('--show');
const paths = args.filter((a) => a !== '--show');
const refuse = (status, msg) => { process.stderr.write(`run-batch: ${msg}\n`); process.exit(status); };
if (paths.length !== 1 || paths[0].startsWith('-')) refuse(2, 'usage: run-batch.mjs [--show] <corpus>');
const path = paths[0];
let text;
try {
  text = readFileSync(path, 'utf8');
} catch (e) {
  refuse(2, `cannot read ${path}: ${e.code ?? e.message}`);
}

const programs = readCorpus(text);
if (programs.length === 0) refuse(1, `no programs in ${path}: a corpus is \`### \` records`);
const lines = [];
for (const src of programs) {
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
