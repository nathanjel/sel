import { Value } from '../value.mjs';
import { define } from '../registry.mjs';

define({
  name: 'IS_NULL', min: 1, max: 1,
  fn: (args) => Value.bool(args.val(0).isNull()),
});

define({
  name: 'IS_NOT_NULL', min: 1, max: 1,
  fn: (args) => Value.bool(!args.val(0).isNull()),
});

define({
  name: 'COALESCE', min: 1, max: Infinity, lazy: true,
  fn: (args) => {
    const n = args.count();
    for (let i = 0; i < n; i++) {
      const v = args.val(i);
      if (!v.isNull()) return v;
    }
    return Value.null();
  },
});

define({
  name: 'GET', min: 2, max: 3, lazy: true,
  fn: (args) => {
    const target = args.val(0);
    const key = args.text(1);
    if (!target.isNull() && target.has(key)) {
      const v = target.get(key);
      if (v !== undefined) return v;
    }
    if (args.count() > 2) return args.val(2);
    return Value.null();
  },
});

define({
  name: 'PATH', min: 2, max: 3, lazy: true,
  fn: (args) => {
    const target = args.val(0);
    const pathStr = args.text(1);
    if (pathStr === '') return target;
    const segments = pathStr.split('.');
    let cur = target;
    for (const seg of segments) {
      if (cur.isNull() || !cur.has(seg)) {
        if (args.count() > 2) return args.val(2);
        return Value.null();
      }
      cur = cur.get(seg);
      if (cur === undefined) {
        if (args.count() > 2) return args.val(2);
        return Value.null();
      }
    }
    return cur;
  },
});

define({
  name: 'IS_BLANK', min: 1, max: 1,
  fn: (args) => Value.bool(args.val(0).isVacuous()),
});

define({
  name: 'IS_PRESENT', min: 1, max: 1,
  fn: (args) => Value.bool(!args.val(0).isVacuous()),
});
