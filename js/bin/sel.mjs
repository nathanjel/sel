#!/usr/bin/env node
// SEL command line: evaluate an expression, a file, or start a REPL.
//
//   sel.mjs -e 'EXPR'          evaluate and print
//   sel.mjs file.sel           evaluate a file
//   sel.mjs --deps -e 'EXPR'   print the variables the expression reads
//   sel.mjs                    REPL, keeping one context across lines

import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { compile, Value, SelError, functionNames } from '../src/sel.mjs';

function show(v) {
  if (v.size === 0) {
    if (v.kind === 'TEXT') return v.scalar;
    if (v.kind === 'BOOL') return v.scalar ? 'TRUE' : 'FALSE';
    if (v.kind === 'BIN') return `bin:${v.dump().slice(1)}`;
  }
  return v.dump();
}

function report(e) {
  if (!(e instanceof SelError)) throw e;
  process.stderr.write(`${e.code} at line ${e.line} column ${e.col}: ${e.message}\n`);
}

const argv = process.argv.slice(2);
const wantDeps = argv.includes('--deps');
const args = argv.filter((a) => a !== '--deps');

if (args[0] === '--functions') {
  console.log(functionNames().join('\n'));
  process.exit(0);
}

let source = null;
if (args[0] === '-e') source = args[1];
else if (args.length > 0) source = readFileSync(args[0], 'utf8');

if (source !== null) {
  try {
    const program = compile(source);
    if (wantDeps) {
      console.log(program.dependencies().join('\n'));
    } else {
      console.log(show(program.run(Value.none())));
    }
  } catch (e) {
    report(e);
    process.exit(1);
  }
} else {
  // REPL: one context for the whole session, so assignments persist.
  const root = Value.none();
  const rl = createInterface({ input: process.stdin, output: process.stdout, prompt: 'sel> ' });
  rl.prompt();
  rl.on('line', (line) => {
    if (line.trim()) {
      try {
        console.log(show(compile(line).run(root)));
      } catch (e) {
        report(e);
      }
    }
    rl.prompt();
  });
  rl.on('close', () => process.stdout.write('\n'));
}
