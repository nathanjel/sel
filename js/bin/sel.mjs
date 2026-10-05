#!/usr/bin/env node
// SEL command line: evaluate an expression, a file, or start a REPL.
//
//   sel.mjs -e 'EXPR'          evaluate and print
//   sel.mjs file.sel           evaluate a file
//   sel.mjs --deps -e 'EXPR'   print the variables the expression reads
//   sel.mjs                    REPL, keeping one context across lines
//   sel.mjs --help | --version
//
// The contract every host's CLI meets (exit statuses, streams, the prompt only
// on a terminal, what a blank line is) is in docs/usage/repl.md.

import { readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { compile, Value, SelError, functionNames } from '../src/sel.mjs';
import { decodeSource } from '../src/utf8.mjs';
import { show } from './show.mjs';

function report(e) {
  if (!(e instanceof SelError)) throw e;
  process.stderr.write(`${e.code} at line ${e.line} column ${e.col}: ${e.message}\n`);
}

// A file that cannot be read (missing, a directory, unreadable): one plain line
// on stderr and exit status 1, not a stack trace.
function cannotRead(path, e) {
  process.stderr.write(`sel: cannot read ${path}: ${e && e.code ? e.code : 'error'}\n`);
  process.exit(1);
}

// A command line that does not say what to run: exit status 2 (docs/usage/repl.md).
function usageError(message) {
  process.stderr.write(`sel: ${message}\n`);
  process.exit(2);
}

const USAGE = `usage: sel [--deps] -e EXPR     evaluate EXPR and print the result
       sel [--deps] FILE        evaluate the program in FILE
       sel                      read programs from stdin, one per line
options:
  -e EXPR       the program to evaluate
  --deps        print the variables the program reads, one per line, instead
  -h, --help    print this text
  --version     print the version
`;

function version() {
  const pkg = JSON.parse(readFileSync(new URL('../../package.json', import.meta.url), 'utf8'));
  return pkg.version;
}

const argv = process.argv.slice(2);
let wantDeps = false;
let expr = null;
let file = null;
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  if (a === '-h' || a === '--help') {
    process.stdout.write(USAGE);
    process.exit(0);
  } else if (a === '--version') {
    process.stdout.write(`sel ${version()}\n`);
    process.exit(0);
  } else if (a === '--functions') {
    console.log(functionNames().join('\n'));
    process.exit(0);
  } else if (a === '--deps') {
    wantDeps = true;
  } else if (a === '-e') {
    if (i + 1 >= argv.length) usageError('-e needs an expression');
    if (expr !== null || file !== null) usageError(`unexpected argument ${argv[i + 1]}`);
    expr = argv[++i];
  } else if (a.length > 1 && a.startsWith('-')) {
    usageError(`unknown option ${a}`);
  } else if (expr !== null || file !== null) {
    usageError(`unexpected argument ${a}`);
  } else {
    file = a;
  }
}
if (wantDeps && expr === null && file === null) usageError('--deps needs -e EXPR or a FILE');

let source = expr;
// Bytes in, strictly: no replacement character, no newline translation. An
// invalid file is E_UTF8 at its first bad byte (SPEC §2), not a mangled program.
if (file !== null) {
  let bytes;
  try {
    bytes = readFileSync(file);
  } catch (e) {
    cannotRead(file, e);
  }
  try {
    source = decodeSource(bytes);
  } catch (e) {
    report(e);
    process.exit(1);
  }
}

if (source !== null) {
  try {
    const program = compile(source);
    if (wantDeps) {
      // One name per line; no dependencies prints nothing, not an empty line.
      for (const name of program.dependencies()) process.stdout.write(`${name}\n`);
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
  // Blank means SEL whitespace only (space, TAB, CR, LF): a line of NBSP or
  // U+3000 is a program, and the lexer says what is wrong with it.
  const evalLine = (line) => {
    if (!/^[ \t\r\n]*$/.test(line)) {
      try {
        console.log(show(compile(line).run(root)));
      } catch (e) {
        report(e);
      }
    }
  };
  if (process.stdin.isTTY) {
    const rl = createInterface({ input: process.stdin, output: process.stdout, prompt: 'sel> ' });
    rl.prompt();
    rl.on('line', (line) => { evalLine(line); rl.prompt(); });
    rl.on('close', () => process.stdout.write('\n'));
  } else {
    // A pipe carries bytes, and readline would decode them leniently on the way
    // in. Split on LF ourselves and decode each line strictly.
    let pending = Buffer.alloc(0);
    const flush = (bytes) => {
      try { evalLine(decodeSource(bytes)); } catch (e) { report(e); }
    };
    process.stdin.on('data', (chunk) => {
      pending = Buffer.concat([pending, chunk]);
      let nl;
      while ((nl = pending.indexOf(10)) >= 0) {
        flush(pending.subarray(0, nl));
        pending = pending.subarray(nl + 1);
      }
    });
    // Nothing but results and errors on a pipe: no prompt, no closing newline.
    process.stdin.on('end', () => {
      if (pending.length > 0) flush(pending);
    });
  }
}
