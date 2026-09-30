// JS-P25: textLiteral over large text (inline mode): per-character scan vs one regex pass.
import { bench, rng } from './lib.mjs';
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const entry = process.env.SEL_JS_ENTRY ? resolve(dirname(process.env.SEL_JS_ENTRY), 'sql/emit.mjs') : resolve(ROOT, 'js/src/sql/emit.mjs');
const { textLiteral } = await import(pathToFileURL(entry).href);
const r = rng(25);
const alphabet = "abcdefgh '\\ijkl\n ";
const mk = (n) => { let s = ''; for (let i = 0; i < n; i++) s += alphabet[Math.floor(r() * alphabet.length)]; return s; };
const big = mk(2800000), mid = mk(20000), small = mk(40);
for (const d of ['mariadb', 'postgresql']) {
  bench(`P25 textLiteral ${d} 2.8M chars`, () => textLiteral(d, big).length, { reps: 5, warm: 1 });
  bench(`P25 textLiteral ${d} 20k chars x50`, () => { let n = 0; for (let i = 0; i < 50; i++) n += textLiteral(d, mid).length; return n; });
  bench(`P25 textLiteral ${d} 40 chars x200000`, () => { let n = 0; for (let i = 0; i < 200000; i++) n += textLiteral(d, small).length; return n; });
}
