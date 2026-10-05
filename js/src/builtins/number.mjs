import * as D from '../decimal.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';
import { checkSizedInt, MAX_SCALE, MAX_POWER } from '../budget.mjs';


define({ name: 'ABS', min: 1, max: 1, fn: (a) => Value.numOwned(D.abs(a.dec(0))) });
define({ name: 'SIGN', min: 1, max: 1, fn: (a) => Value.int(D.sign(a.dec(0))) });
define({ name: 'CEIL', min: 1, max: 1, fn: (a) => Value.numOwned(D.ceil(a.dec(0), a.pos)) });
define({ name: 'FLOOR', min: 1, max: 1, fn: (a) => Value.numOwned(D.floor(a.dec(0), a.pos)) });
define({ name: 'TRUNC', min: 1, max: 1, fn: (a) => Value.numOwned(D.trunc(a.dec(0))) });
define({ name: 'CANON', min: 1, max: 1, fn: (a) => Value.numOwned(D.trimScale(a.dec(0))) });

define({
  name: 'ROUND', min: 2, max: 2,
  fn: (a) => Value.numOwned(D.round(a.dec(0), checkSizedInt(a.dec(1), 'ROUND', 2, MAX_SCALE, 'ROUND scale', a.posOf(1)), a.pos)),
});

define({
  name: 'POWER', min: 2, max: 2,
  fn: (a) => Value.numOwned(D.power(a.dec(0), checkSizedInt(a.dec(1), 'POWER', 2, MAX_POWER, 'POWER exponent', a.posOf(1)), a.pos)),
});

define({
  name: 'MIN', min: 1, max: Infinity,
  fn: (args) => {
    let best = args.dec(0);
    for (let i = 1; i < args.count(); i++) {
      const d = args.dec(i);
      if (D.cmp(d, best) < 0) best = d;
    }
    return Value.numOwned(best);
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
    return Value.numOwned(best);
  },
});

// The non-throwing probe. Every other numeric path raises E_NOT_NUM instead.
define({ name: 'ISNUM', min: 1, max: 1, fn: (a) => Value.bool(a.val(0).looksNumeric()) });
