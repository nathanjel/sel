import { fail } from '../errors.mjs';
import * as D from '../decimal.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';

// spec/SPEC.md §6.4. Without these, a size argument nobody meant to write takes
// down the host instead of failing as a rule error — and POWER quietly returned
// a wrong answer here, because `e >>= 1` in the decimal core truncates the
// exponent to 32 bits.
const MAX_SCALE = 1000000;
const MAX_POWER = 100000;

function sized(args, i, limit, what) {
  const n = args.nonNegInt(i);
  if (n > limit) {
    fail('E_RANGE', `${what} ${n} exceeds the maximum of ${limit}`, args.posOf(i));
  }
  return n;
}

define({ name: 'ABS', min: 1, max: 1, fn: (a) => Value.num(D.abs(a.dec(0))) });
define({ name: 'SIGN', min: 1, max: 1, fn: (a) => Value.int(D.sign(a.dec(0))) });
define({ name: 'CEIL', min: 1, max: 1, fn: (a) => Value.num(D.ceil(a.dec(0))) });
define({ name: 'FLOOR', min: 1, max: 1, fn: (a) => Value.num(D.floor(a.dec(0))) });
define({ name: 'TRUNC', min: 1, max: 1, fn: (a) => Value.num(D.trunc(a.dec(0))) });

define({
  name: 'ROUND', min: 2, max: 2,
  fn: (a) => Value.num(D.round(a.dec(0), sized(a, 1, MAX_SCALE, 'ROUND scale'))),
});

define({
  name: 'POWER', min: 2, max: 2,
  fn: (a) => Value.num(D.power(a.dec(0), sized(a, 1, MAX_POWER, 'POWER exponent'))),
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
