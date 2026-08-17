import { Value } from '../value.mjs';
import { define } from '../registry.mjs';

define({ name: 'COUNT', min: 1, max: 1, fn: (args) => Value.int(args.val(0).size()) });

define({
  name: 'INDEXES', min: 1, max: 1,
  fn: (args) => Value.list(args.val(0).keys().map(Value.text)),
});

define({
  name: 'HAS', min: 2, max: 2,
  fn: (args) => Value.bool(args.val(0).has(args.text(1))),
});
