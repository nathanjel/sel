// JS-P25 differential: the regex-pass textLiteral against the per-character one, every
// dialect, random text around the switch-over length. Usage:
//   node tools/perf/js/p25-text-literal-diff.mjs <baseline js/src/sql/emit.mjs>
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { rng } from './lib.mjs';
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const now = await import(pathToFileURL(resolve(ROOT, 'js/src/sql/emit.mjs')).href);
const base = await import(pathToFileURL(resolve(process.argv[2])).href);
const r = rng(2525);
const alphabet = ["'", '\\', '\\\\', "''", 'a', 'b', ' ', '\n', '\0', '😀', 'é', '%', '_', '"', ' '];
let n = 0, bad = 0;
for (let i = 0; i < 60000; i++) {
  const len = Math.floor(r() * 140);
  let s = '';
  for (let k = 0; k < len; k++) s += alphabet[Math.floor(r() * alphabet.length)];
  for (const d of ['mariadb', 'mysql', 'postgresql', 'sqlite', 'ansi']) {
    n++;
    if (base.textLiteral(d, s) !== now.textLiteral(d, s)) { if (bad++ < 3) console.log('DIFF', d, JSON.stringify(s)); }
  }
}
console.log(`${n} comparisons, ${bad} differences`);
