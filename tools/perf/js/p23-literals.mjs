// JS-P23: numeric/text literal evaluation cost (a fresh Value per evaluation).
import { sel, bench, rng } from './lib.mjs';
const r = rng(23);
const N = Number(process.env.P23_N || 200000);
const rows = Array.from({ length: N }, (_, i) => ({ v: Math.floor(r() * 100), t: 'k' + (i % 7) }));
const f1 = sel.compile('COUNT(FILTER(ROWS, _["v"] > 50))');
const f2 = sel.compile('COUNT(FILTER(ROWS, _["v"] > 50 AND _["v"] < 90 AND _["t"] $== "k3"))');
const m1 = sel.compile('SUM(ROWS, _["v"] * 3 + 1)');
bench(`P23 FILTER v > 50 over ${N} rows`, () => f1.run({ ROWS: rows }).scalar);
bench(`P23 FILTER 3 literal compares over ${N} rows`, () => f2.run({ ROWS: rows }).scalar);
bench(`P23 SUM v*3+1 over ${N} rows`, () => m1.run({ ROWS: rows }).scalar);
