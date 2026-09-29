#!/usr/bin/env node
// Driver for tools/check-regex-ambiguity-diff.py: one pattern per line on stdin,
// one verdict per line on stdout -- `A` (accepted by the validator) or `R`
// (refused with E_REGEX_SYNTAX). Any other outcome prints `X:<what>` so a
// mismatch shows it. A last argument `i` checks under the i flag.
//
//   node js/bin/regex-verdict.mjs [i] < patterns.txt

import { readFileSync } from 'node:fs';
import '../src/sel.mjs';                       // registers the builtins
import { validate } from '../src/builtins/regex.mjs';
import { SelError } from '../src/errors.mjs';

const ic = process.argv[2] === 'i';
const lines = readFileSync(0, 'utf8').split('\n');
if (lines[lines.length - 1] === '') lines.pop();
const out = [];
for (const pattern of lines) {
  try {
    validate(pattern, { line: 1, col: 1, offset: 0 }, ic);
    out.push('A');
  } catch (e) {
    if (e instanceof SelError && e.code === 'E_REGEX_SYNTAX') out.push('R');
    else out.push(`X:${e && e.code ? e.code : e}`);
  }
}
process.stdout.write(out.join('\n') + '\n');
