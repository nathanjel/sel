// JS-P21: the regex compile cache must stay bounded under data-derived patterns.
import { sel, bench } from './lib.mjs';
const N = Number(process.env.P21_N || 300000);
const p = sel.compile('RMATCH(PAT, "abc")');
const gc = globalThis.gc;
if (gc) gc();
const before = process.memoryUsage().heapUsed;
let checksum = 0;
const t0 = process.hrtime.bigint();
for (let i = 0; i < N; i++) checksum += p.run({ PAT: 'a' + i + 'b?c*' }).scalar === 'TRUE' ? 1 : 0;
const ms = Number(process.hrtime.bigint() - t0) / 1e6;
if (gc) gc();
const after = process.memoryUsage().heapUsed;
console.log(`P21 ${N} distinct patterns: ${ms.toFixed(0)} ms, retained ${((after - before) / 1048576).toFixed(1)} MB (run with --expose-gc), checksum ${checksum}`);
