import { fail } from '../errors.mjs';
import * as D from '../decimal.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';

// spec/SPEC.md §6.4. Without these, a size argument nobody meant to write takes
// down the host instead of failing as a rule error — and POWER quietly returned
// a wrong answer here, because `e >>= 1` in the decimal core truncates the
// exponent to 32 bits.
export const MAX_SCALE = 1000000;
export const MAX_POWER = 100000;

export function checkSizedInt(d, name, argNum, limit, what, pos) {
  if (!D.isInteger(d)) fail('E_NOT_INT', `${name} argument ${argNum} must be a whole number`, pos);
  const n = D.toSafeInt(d);
  if (n < 0) fail('E_RANGE', `${name} argument ${argNum} must not be negative`, pos);
  if (n > limit) fail('E_RANGE', `${what} ${n} exceeds the maximum of ${limit}`, pos);
  return n;
}

define({ name: 'ABS', min: 1, max: 1, fn: (a) => Value.num(D.abs(a.dec(0))) });
define({ name: 'SIGN', min: 1, max: 1, fn: (a) => Value.int(D.sign(a.dec(0))) });
define({ name: 'CEIL', min: 1, max: 1, fn: (a) => Value.num(D.ceil(a.dec(0), a.pos)) });
define({ name: 'FLOOR', min: 1, max: 1, fn: (a) => Value.num(D.floor(a.dec(0), a.pos)) });
define({ name: 'TRUNC', min: 1, max: 1, fn: (a) => Value.num(D.trunc(a.dec(0))) });
define({ name: 'CANON', min: 1, max: 1, fn: (a) => Value.num(D.trimScale(a.dec(0))) });

define({
  name: 'ROUND', min: 2, max: 2,
  fn: (a) => Value.num(D.round(a.dec(0), checkSizedInt(a.dec(1), 'ROUND', 2, MAX_SCALE, 'ROUND scale', a.posOf(1)), a.pos)),
});

define({
  name: 'POWER', min: 2, max: 2,
  fn: (a) => Value.num(D.power(a.dec(0), checkSizedInt(a.dec(1), 'POWER', 2, MAX_POWER, 'POWER exponent', a.posOf(1)), a.pos)),
});

define({
  name: 'MIN', min: 1, max: Infinity,
  fn: (args) => {
    let best = args.dec(0);
    for (let i = 1; i < args.count(); i++) {
      const d = args.dec(i);
      if (D.cmp(d, best) < 0) best = d;
    }
    return Value.num(best);
  },
});

define({
  name: 'MAX', min: 1, max: Infinity,
  fn: (args) => {
    let best = args.dec(0);
    for (let i = 1; i < args.count(); i++) {
      const d = args.dec(i);
      if (D.cmp(d, best) > 0) best = d;
    }
    return Value.num(best);
  },
});

// The non-throwing probe. Every other numeric path raises E_NOT_NUM instead.
define({ name: 'ISNUM', min: 1, max: 1, fn: (a) => Value.bool(a.val(0).looksNumeric()) });
