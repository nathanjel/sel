// Goes in the relevant js/src/builtins/*.mjs. Not a runnable file: this is a fragment that
// compiles only in place. See README.md beside it.
// EXAMPLE-BEGIN
import { define } from '../registry.mjs';
import { Value, NONE } from '../value.mjs';

define({
  name: 'FIRST', min: 2, max: 3, lazy: true, binds: true,
  fn: (args, ctx) => {
    const three = args.count() === 3;
    const binder = three ? args.symbol(1) : '_';
    const body = args.node(three ? 2 : 1);

    const list = args.val(0);
    const items = list.size > 0 ? list.entries()
      : list.kind === NONE ? [] : [['1', list]];

    for (const [key, item] of items) {
      ctx.pushFrame(new Map([[binder, item], ['_K', Value.text(key)]]));
      try {
        if (args.evalNode(body).asBool(body.pos)) return item.clone();
      } finally {
        ctx.popFrame();
      }
    }
    return Value.text('');
  },
});
// EXAMPLE-END
