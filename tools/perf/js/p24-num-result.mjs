// JS-P24: an arithmetic result was validated twice (D.add's guard, then Value.num's checkDecimal).
import { sel, bench, rng, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const r = rng(24);
const N = Number(process.env.P24_N || 300000);
const rows = Array.from({ length: N }, () => ({ v: Math.floor(r() * 1000), w: Math.floor(r() * 1000) }));
const acc = sel.compile('T = 0; SUM(ROWS, (T += _["v"] * 2; 1))');
const neg = sel.compile('SUM(ROWS, (U = -_["v"]; 1))');
const mix = sel.compile('COUNT(FILTER(ROWS, (_["v"] + _["w"]) % 7 == 3 AND IF(_["v"] > 10, 1, 0) + 0 == 1))');
bench(`P24 compound assignment T += v*2 over ${N} rows`, () => acc.run({ ROWS: rows }).scalar);
bench(`P24 unary minus + assignment over ${N} rows`, () => neg.run({ ROWS: rows }).scalar);
bench(`P24 mixed arithmetic filter over ${N} rows`, () => mix.run({ ROWS: rows }).scalar);
