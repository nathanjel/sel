// JS-P28 sub-items: the pow10 cache clear, and D.cmp at very different scales.
import { bench, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
const base = process.env.SEL_JS_ENTRY ? dirname(process.env.SEL_JS_ENTRY) : resolve(root, 'js/src');
const D = await import(pathToFileURL(resolve(base, 'decimal.mjs')).href);
bench('P28 pow10 alternating scales 600000 / 500000 x20', () => { let n = 0; for (let i = 0; i < 20; i++) n += D.pow10(i % 2 ? 500000 : 600000) > 0n ? 1 : 0; return n; }, { reps: 3, warm: 1 });
const a = D.parse('0.' + '1'.repeat(600000)), b = D.parse('0.' + '2'.repeat(10));
bench('P28 cmp(600000-digit scale vs scale 10) x20', () => { let n = 0; for (let i = 0; i < 20; i++) n += D.cmp(a, b); return n; }, { reps: 3, warm: 1 });
const c = D.parse('12.5'), d = D.parse('12.50');
bench('P28 cmp(12.5, 12.50) x1M', () => { let n = 0; for (let i = 0; i < 1000000; i++) n += D.cmp(c, d); return n; }, { reps: 5 });
