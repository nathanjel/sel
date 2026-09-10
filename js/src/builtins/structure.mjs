import { Value, NONE } from '../value.mjs';
import { define } from '../registry.mjs';

function elements(value) {
  if (value.size() > 0) return value.entries();
  return value.kind === NONE ? [] : [['1', value]];
}

define({ name: 'COUNT', min: 1, max: 1, fn: (args) => Value.int(args.val(0).size()) });

define({
  name: 'INDEXES', min: 1, max: 1,
  fn: (args) => Value.list(args.val(0).keys().map(Value.text)),
});

define({
  name: 'HAS', min: 2, max: 2,
  fn: (args) => Value.bool(args.val(0).has(args.text(1))),
});

define({
  name: 'LIST', min: 0, max: Infinity,
  fn: (args) => {
    const n = args.count();
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push(args.val(i).clone());
    }
    return Value.list(out);
  },
});

define({
  name: 'RECORD', min: 0, max: Infinity,
  arityError: (count) => count % 2 !== 0 ? `RECORD takes an even number of arguments (key-value pairs), got ${count}` : null,
  fn: (args) => {
    const rec = Value.none();
    const n = args.count();
    for (let i = 0; i < n; i += 2) {
      rec.set(args.text(i), args.val(i + 1).clone());
    }
    return rec;
  },
});

define({
  name: 'TAKE', min: 2, max: 2,
  fn: (args) => {
    const val = args.val(0);
    const count = args.nonNegInt(1);
    if (count === 0 || val.isNull()) return Value.list([]);
    const entries = elements(val);
    const limit = Math.min(count, entries.length);
    const out = [];
    for (let i = 0; i < limit; i++) {
      out.push(entries[i][1].clone());
    }
    return Value.list(out);
  },
});

define({
  name: 'DROP', min: 2, max: 2,
  fn: (args) => {
    const val = args.val(0);
    const count = args.nonNegInt(1);
    if (val.isNull()) return Value.list([]);
    const entries = elements(val);
    if (count >= entries.length) return Value.list([]);
    const out = [];
    for (let i = count; i < entries.length; i++) {
      out.push(entries[i][1].clone());
    }
    return Value.list(out);
  },
});

define({
  name: 'SELECT_COLS', min: 2, max: Infinity,
  fn: (args) => {
    const val = args.val(0);
    if (val.isNull()) return Value.list([]);
    const colCount = args.count();
    const cols = [];
    for (let i = 1; i < colCount; i++) cols.push(args.text(i));
    const entries = elements(val);
    const out = [];
    for (const [, row] of entries) {
      const newRow = Value.none();
      for (const c of cols) {
        if (row.has(c)) newRow.set(c, row.get(c).clone());
      }
      out.push(newRow);
    }
    return Value.list(out);
  },
});

define({
  name: 'DISTINCT', min: 1, max: 1,
  fn: (args) => {
    const val = args.val(0);
    if (val.isNull()) return Value.list([]);
    const entries = elements(val);
    const out = [];
    for (const [, item] of entries) {
      let seen = false;
      for (const existing of out) {
        if (item.eql(existing)) { seen = true; break; }
      }
      if (!seen) out.push(item.clone());
    }
    return Value.list(out);
  },
});

