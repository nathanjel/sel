// JS-P18 differential: the bracketed guard must give the exact verdict of the old exact-count guard,
// for magnitudes right at the integer-digit cap and for scales around it.
import { pathToFileURL } from 'node:url';
import { root } from './lib.mjs';
const NEW = await import(pathToFileURL(root + '/js/src/decimal.mjs').href);
const OLD = await import(pathToFileURL(process.env.OLD_DEC).href);
let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const verdict = (D, d) => { try { D.guard(d, null); return 'ok'; } catch (e) { return e.code; } };
let bad = 0, n = 0;
const cap = NEW.MAX_INT_DIGITS;
for (let t = 0; t < 400; t++) {
  const digits = cap - 6 + Math.floor(rnd() * 14);            // 999994 .. 1000007 digits
  const lead = BigInt(1 + Math.floor(rnd() * 9));
  const mag = lead * (10n ** BigInt(digits - 1)) + BigInt(Math.floor(rnd() * 1e6));
  for (const scale of [0, 1, 3, 10]) {
    const d = { neg: false, digits: mag, scale };
    n++; const a = verdict(OLD, d), b = verdict(NEW, d);
    if (a !== b) { bad++; console.log('DIFF digits', digits, 'scale', scale, a, b); }
  }
}
// powers of two and ten-edge values
for (const k of [3321920, 3321927, 3321928, 3321929, 3321930, 3321940]) for (const off of [-1n, 0n, 1n]) {
  const mag = (1n << BigInt(k)) + off; n++;
  const a = verdict(OLD, { neg: false, digits: mag, scale: 0 }), b = verdict(NEW, { neg: false, digits: mag, scale: 0 });
  if (a !== b) { bad++; console.log('DIFF 2^', k, off, a, b); }
}
for (const e of [cap - 1, cap, cap + 1]) for (const off of [-1n, 0n]) {
  const mag = 10n ** BigInt(e) + off; n++;
  const a = verdict(OLD, { neg: false, digits: mag, scale: 0 }), b = verdict(NEW, { neg: false, digits: mag, scale: 0 });
  if (a !== b) { bad++; console.log('DIFF 10^', e, off, a, b); }
}
console.log(`${n} values, ${bad} differences`);
