import assert from 'node:assert/strict';
import * as D from '../js/src/decimal.mjs';
const one = D.fromInt(1);
let checks=0;
function check(fn) { fn(); checks++; }
const oldToString=BigInt.prototype.toString;
try {
  BigInt.prototype.toString=function(radix) {
    throw new Error('a below-cap arithmetic guard must not render a BigInt');
  };
  for(const n of [24,1000,100000]) {
    const digits=10n ** BigInt(n);
    check(()=>assert.equal(D.add({neg:false,digits,scale:0},one).digits,digits+1n));
    check(()=>assert.equal(D.mul({neg:false,digits,scale:0},D.fromInt(2)).digits,digits*2n));
  }
} finally { BigInt.prototype.toString=oldToString; }
const pos={line:3,col:7,offset:12};
const tooWide=10n ** BigInt(D.MAX_INT_DIGITS);
const max={neg:false,digits:tooWide-1n,scale:0};
check(()=>assert.equal(D.add(max,D.ZERO).digits,max.digits));
check(()=>assert.throws(()=>D.add(max,one,pos),e=>e.code==='E_RANGE' && e.line===3 && e.col===7));
// The bit threshold is conservative: numbers immediately around it remain legal.
const threshold=1n<<3321928n;
for(const digits of [threshold-1n,threshold,threshold+1n]) {
  check(()=>assert.equal(D.add({neg:false,digits,scale:0},one).digits,digits+1n));
}
// Fractional scale counts separately from integer width.
check(()=>assert.equal(D.add({neg:true,digits:tooWide,scale:1},D.ZERO).scale,1));
check(()=>assert.equal(D.round(max,D.MAX_FRAC_DIGITS).scale,D.MAX_FRAC_DIGITS));
check(()=>assert.throws(()=>D.round(one,D.MAX_FRAC_DIGITS+1,pos),e=>e.code==='E_RANGE' && e.col===7));
console.log(`JS decimal guard: ${checks} checks passed`);
