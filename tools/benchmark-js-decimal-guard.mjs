// node tools/benchmark-js-decimal-guard.mjs [/path/to/decimal.mjs]
import { performance } from 'node:perf_hooks';
const D = await import(process.argv[2] ?? '../js/src/decimal.mjs');
const one = D.fromInt(1);
const results = {};
for (const digits of [12, 24, 1000, 10000, 100000]) {
  const a = D.parse('9'.repeat(digits));
  const count = digits < 10000 ? 20000 : digits < 100000 ? 2000 : 200;
  for (let i=0;i<1000;i++) D.add(a,one);
  const samples=[];
  for(let r=0;r<7;r++) {
    const start=performance.now();
    for(let i=0;i<count;i++) D.add(a,one);
    samples.push((performance.now()-start)*1000/count);
  }
  samples.sort((a,b)=>a-b);
  results[digits]={median_us:samples[3],samples_us:samples};
}
console.log(JSON.stringify({node:process.version,results},null,2));
