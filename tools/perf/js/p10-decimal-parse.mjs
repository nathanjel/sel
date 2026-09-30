// JS-P10: D.parse on short numerals, and a numeric-data workload.
import { sel, bench, rng, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const D = await import(pathToFileURL(root + '/js/src/decimal.mjs').href);
for (const t of ['12.50', '3', '-0.05', '123456789012345', '1234567890123456789.123']) {
  bench(`P10 parse(${t}) x1e6`, () => { let n = 0; for (let i = 0; i < 1e6; i++) n += D.parse(t, null).scale; return n; }, { reps: 5, warm: 1 });
}
const r = rng(9);
const nums = Array.from({ length: 200000 }, () => String((r() * 1e5) | 0) + (r() < 0.5 ? '.' + ((r() * 100) | 0) : ''));
bench('P10 ALL(L, _ * 2 + 1 > 0) 200k numeric strings', () => sel.compile('ALL(L, _ * 2 + 1 > 0)').run({ L: nums }).scalar, { reps: 3, warm: 1 });
