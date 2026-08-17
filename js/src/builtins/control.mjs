import { fail } from '../errors.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';

// The whole of SEL's control flow. Lazy, so only the taken branch is evaluated —
// exactly the property the AST calling convention exists to provide.
define({
  name: 'IF', min: 2, max: 3, lazy: true,
  fn: (args) => {
    if (args.bool(0)) return args.val(1);
    if (args.count() === 3) return args.val(2);
    return Value.text('');
  },
});

// Flat multi-branch selection — sugar for a nested IF ladder, with exactly the
// same laziness: conditions are evaluated in order, and only the result that
// matches is evaluated at all.
//
// The argument count must be odd: condition/result pairs plus a mandatory
// default. IF can safely let its two-argument form default to "" because there
// is one branch and nothing to mis-pair, but with an even count here a single
// miscounted comma would shift every pair by one and still compile. Requiring
// the default turns that into a compile-time E_ARITY instead of a wrong answer.
define({
  name: 'COND', min: 3, max: Infinity, lazy: true,
  arityError: (n) => (n % 2 === 0
    ? `COND takes condition/result pairs and a final default (an odd number of arguments), got ${n}`
    : null),
  fn: (args) => {
    const last = args.count() - 1;
    for (let i = 0; i < last; i += 2) {
      if (args.bool(i)) return args.val(i + 1);
    }
    return args.val(last);
  },
});

// The one error a rule author raises deliberately.
define({
  name: 'ABORT', min: 1, max: 1,
  fn: (args) => fail('E_ABORT', args.text(0), args.posOf(0)),
});
