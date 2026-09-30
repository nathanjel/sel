// JS-P18: rejecting an over-cap number must not cost a 10^k power per rejection.
import { sel, bench } from './lib.mjs';
const cases = ['POWER(POWER(3, 99999), 21)', 'POWER(999999999999, 100000)', 'REPEAT("9", 1000000) * REPEAT("9", 1000000)'];
for (const src of cases) {
  const p = sel.compile(src);
  bench('P18 ' + src.slice(0, 40), () => { try { return p.run({}).scalar; } catch (e) { return e.code; } }, { reps: 5, warm: 1 });
}
const ok = sel.compile('POWER(3, 99999) + 1');
bench('P18 in-range control POWER(3,99999)+1', () => ok.run({}).scalar.length, { reps: 5, warm: 1 });
const small = sel.compile('123456789012345678901234567890 * 98765432109876543210');
bench('P18 ordinary 50-digit product x100000', () => { let n = 0; for (let i = 0; i < 100000; i++) n += small.run({}).scalar.length; return n; }, { reps: 5, warm: 1 });
