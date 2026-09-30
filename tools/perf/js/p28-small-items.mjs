// JS-P28: the "smaller items" bundle — structural hash of packed lists / BIN, and the
// per-pair [key, value] allocations of the relational builtins.
import { sel, bench, rng } from './lib.mjs';
const r = rng(28);
const N = Number(process.env.P28_N || 100000);
const lists = Array.from({ length: N }, () => Array.from({ length: 8 }, () => Math.floor(r() * 4)));
const dd = sel.compile('COUNT(DEDUPE(ROWS))');
bench(`P28 DEDUPE over ${N} packed lists of 8`, () => dd.run({ ROWS: lists }).scalar);
const bins = Array.from({ length: N }, () => Array.from({ length: 32 }, () => Math.floor(r() * 3)).map((x) => x));
const ddb = sel.compile('COUNT(DEDUPE(MAP(ROWS, X, LTB(X))))');
bench(`P28 DEDUPE over ${N} BIN values of 32 bytes`, () => ddb.run({ ROWS: bins }).scalar);
const flat = Array.from({ length: 300000 }, (_, i) => i);
const t1 = sel.compile('COUNT(TAKE(DROP(L, 10), 200000))');
bench('P28 TAKE(DROP(...)) over a flat 300k list', () => t1.run({ L: flat }).scalar);
const sc = sel.compile('COUNT(SELECT_COLS(ROWS, "a", "b"))');
const recs = Array.from({ length: 200000 }, (_, i) => ({ a: i, b: i % 7, c: 'x' }));
bench('P28 SELECT_COLS over 200k records', () => sc.run({ ROWS: recs }).scalar);
const bk = sel.compile('COUNT(BUCKET(ROWS, _["b"], COUNT(_)))');
bench('P28 BUCKET (projection form) over 200k records', () => bk.run({ ROWS: recs }).scalar);
const jn = sel.compile('LEN(JOIN(L, ","))');
bench('P28 JOIN over a flat 300k list', () => jn.run({ L: flat }).scalar);
const tu = sel.compile('BLEN(TO_UTF8(S))');
const S = 'zażółć gęślą jaźń '.repeat(50000);
bench(`P28 TO_UTF8 over ${S.length} chars`, () => tu.run({ S }).scalar);
const pd = sel.compile('LEN(PADL(S, 1000000, "0"))');
bench('P28 PADL 1M', () => pd.run({ S: 'abc' }).scalar);
const nopad = sel.compile('LEN(PADL(S, 3, "0"))');
bench('P28 PADL no-op over a 1M-char text', () => nopad.run({ S: 'q'.repeat(1000000) }).scalar);
