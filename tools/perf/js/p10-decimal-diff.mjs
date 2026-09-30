// Differential for JS-P10: D.parse against a saved copy of the previous decimal.mjs
// (SEL_OLD_DECIMAL=/path/decimal.mjs with absolute imports): random numerals and near-numerals.
import { pathToFileURL } from 'node:url';
import { rng } from './lib.mjs';
const old = await import(pathToFileURL(process.env.SEL_OLD_DECIMAL).href);
const nw = await import(pathToFileURL(new URL('../../../js/src/decimal.mjs', import.meta.url).pathname).href);
const r = rng(+process.argv[2] || 3), N = +process.argv[3] || 500000;
const A = ['0', '1', '7', '9', '00', '-', '.', '.', '+', 'e', ' ', '5', '3', '٠', 'é'];
const show = (f) => { try { const d = f(); return d === null ? 'null' : `${d.neg}|${d.digits}|${d.scale}`; } catch (e) { return `ERR ${e.code}`; } };
let bad = 0;
for (let k = 0; k < N; k++) {
  let s = ''; const len = (r() * 18) | 0;
  for (let i = 0; i < len; i++) s += A[(r() * A.length) | 0];
  if (r() < 0.3) s = (r() < 0.5 ? '-' : '') + String((r() * 10 ** ((r() * 17) | 0)) | 0) + (r() < 0.6 ? '.' + ((r() * 1000) | 0) : '');
  const a = show(() => old.parse(s, null)), b = show(() => nw.parse(s, null));
  if (a !== b && bad++ < 8) console.log('DIFF', JSON.stringify(s), a, b);
}
console.log('done', N, 'diffs', bad);
