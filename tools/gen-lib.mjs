// What every Node generator in tools/ shares: the command line, the
// write-or-check loop, and the string-literal escapers of the host languages.
//
// The generators render committed artifacts (CLAUDE.md, "Generated, committed
// artifacts"), so the two ways one can go wrong are writing when nobody asked
// (a generator that took `--help` for "regenerate" once rewrote a hand-edited
// fixture) and escaping a string differently in one generator than in another
// (one Python escaper did no escaping at all). Both live here once.

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
export { cppStr } from './cpp-emit.mjs';

// genArgs(name, usage, { extra }) -> { check, rest }
//
// The command line every generator accepts: no argument writes the artifacts,
// `--check` compares and writes nothing, `--help`/`-h` prints the usage and
// exits 0. Anything else is a usage error (exit 2) -- never a write. `extra` is
// a list of further flags a generator accepts; they come back in `rest`.
export function genArgs(name, usage, { extra = [] } = {}) {
  const args = process.argv.slice(2);
  if (args.includes('--help') || args.includes('-h')) {
    process.stdout.write(usage.endsWith('\n') ? usage : usage + '\n');
    process.exit(0);
  }
  const rest = [];
  let check = false;
  for (const a of args) {
    if (a === '--check') check = true;
    else if (extra.includes(a)) rest.push(a);
    else {
      process.stderr.write(`${name}: unexpected argument ${a}\n${usage.endsWith('\n') ? usage : usage + '\n'}`);
      process.exit(2);
    }
  }
  return { check, rest };
}

// writeOrCheck(name, root, outputs, { check, rerun }) -> number of stale outputs
//
// outputs: [[relative path, text], ...]. Write mode writes only the files whose
// content differs, so a no-op run moves no timestamp (the JS bundle guard and
// tools/check-generated.sh's fallback both compare mtimes). Check mode reports
// each stale or missing file on stderr and exits 1 when there is one.
export function writeOrCheck(name, root, outputs, { check, rerun }) {
  let stale = 0;
  let unchanged = 0;
  for (const [rel, text] of outputs) {
    const path = resolve(root, rel);
    let have = null;
    try { have = readFileSync(path, 'utf8'); } catch { /* absent counts as stale */ }
    if (check) {
      if (have !== text) {
        process.stderr.write(`${name}: ${rel} is ${have === null ? 'missing' : 'stale'}\n`);
        stale++;
      }
    } else if (have === text) {
      unchanged++;
    } else {
      writeFileSync(path, text);
      process.stdout.write(`wrote ${rel}\n`);
    }
  }
  if (check && stale) {
    process.stderr.write(`\nrun: ${rerun}\n`);
    process.exit(1);
  }
  // Said out loud: a run that prints nothing reads as one that did nothing.
  if (!check && unchanged) process.stdout.write(`${unchanged} artifact(s) already current\n`);
  return stale;
}

// --- string literals, one escaper per host language --------------------------
//
// Each is correct for ANY string (control characters, quotes, backslashes,
// non-ASCII), not just for the identifiers most generators happen to feed it.

// JavaScript, and JSON.
export const jsStr = (s) => JSON.stringify(String(s));

// Python: a JSON string is a valid Python literal for every input (\uXXXX and
// \n, \r, \t are Python escapes too; non-ASCII stays as UTF-8 source).
export const pyStr = (s) => JSON.stringify(String(s));

// PHP single-quoted: only \\ and \' are escapes there; every other byte, CR and
// NUL included, is taken literally.
export const phpStr = (s) => "'" + String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";

// Common Lisp: in a string, a backslash escapes the next character, whatever it
// is; everything else is literal.
export const lispStr = (s) => '"' + String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"';

// Go and Rust interpreted string literals: the same escapes, \xNN for the other
// control characters (both read \x as one byte, and these are all ASCII).
function cStyle(s) {
  let out = '"';
  for (const ch of String(s)) {
    if (ch === '\\') out += '\\\\';
    else if (ch === '"') out += '\\"';
    else if (ch === '\n') out += '\\n';
    else if (ch === '\r') out += '\\r';
    else if (ch === '\t') out += '\\t';
    else {
      const code = ch.codePointAt(0);
      out += code < 0x20 || code === 0x7f ? '\\x' + code.toString(16).padStart(2, '0') : ch;
    }
  }
  return out + '"';
}
export const goStr = (s) => (s === null || s === undefined ? '""' : cStyle(s));
export const rustStr = (s) => (s === null || s === undefined ? '""' : cStyle(s));
