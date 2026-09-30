// JS-P17: BTL must not build a BigInt-backed decimal per byte.
import { sel, bench } from './lib.mjs';
const rows = Array.from({ length: 300000 }, (_, i) => ({ k: 'abcdefg'.slice(0, 7) + (i % 10) }));
const p = sel.compile('COUNT(MAP(ROWS, BTL(TO_UTF8(_["k"]))))');
bench('P17 BTL over 300k 8-byte rows', () => p.run({ ROWS: rows }).scalar, { reps: 5, warm: 1 });
const big = sel.compile('COUNT(BTL(TO_UTF8(REPEAT("x", 1000000))))');
bench('P17 BTL of one 1 MB text', () => big.run({}).scalar, { reps: 5, warm: 1 });
const small = sel.compile('COUNT(BTL(TO_UTF8("hello")))');
bench('P17 BTL of 5 bytes x200000 (ordinary)', () => { let n = 0; for (let i = 0; i < 200000; i++) n += Number(small.run({}).scalar); return n; }, { reps: 5, warm: 1 });
