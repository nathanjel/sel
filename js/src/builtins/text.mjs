// Text built-ins. Everything counts code points — never bytes, never UTF-16
// units — so positions and lengths agree with PHP on astral characters.
// Positions are 1-based and 0 means "not found" (§7.5).

import { fail } from '../errors.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';
import { toCodePoints, fromCodePoints } from '../utf8.mjs';

const cps = (s) => toCodePoints(s, null);

function indexOfCp(hay, needle, from) {
  const n = needle.length;
  if (n === 0) return -1;
  outer: for (let i = from; i + n <= hay.length; i++) {
    for (let j = 0; j < n; j++) if (hay[i + j] !== needle[j]) continue outer;
    return i;
  }
  return -1;
}

define({ name: 'LEN', min: 1, max: 1, fn: (args) => Value.int(cps(args.text(0)).length) });

define({
  name: 'LEFT', min: 2, max: 2,
  fn: (args) => Value.text(fromCodePoints(cps(args.text(0)).slice(0, args.nonNegInt(1)))),
});

define({
  name: 'RIGHT', min: 2, max: 2,
  fn: (args) => {
    const c = cps(args.text(0));
    const n = args.nonNegInt(1);
    return Value.text(fromCodePoints(c.slice(Math.max(0, c.length - n))));
  },
});

define({
  name: 'SUBSTR', min: 2, max: 3,
  fn: (args) => {
    const c = cps(args.text(0));
    const start = args.int(1);
    if (start < 1) fail('E_RANGE', 'SUBSTR start is 1-based and must be at least 1', args.posOf(1));
    const from = start - 1;
    if (args.count() === 2) return Value.text(fromCodePoints(c.slice(from)));
    return Value.text(fromCodePoints(c.slice(from, from + args.nonNegInt(2))));
  },
});

define({
  name: 'FIND', min: 2, max: 3,
  fn: (args) => {
    const needle = cps(args.text(0));
    const hay = cps(args.text(1));
    let from = 0;
    if (args.count() === 3) {
      const f = args.int(2);
      if (f < 1) fail('E_RANGE', 'FIND start is 1-based and must be at least 1', args.posOf(2));
      from = f - 1;
    }
    if (needle.length === 0) fail('E_BAD_ARG', 'FIND needle must not be empty', args.posOf(0));
    return Value.int(indexOfCp(hay, needle, from) + 1);
  },
});

define({
  name: 'REPLACE', min: 3, max: 3,
  fn: (args) => {
    const needle = cps(args.text(0));
    const repl = cps(args.text(1));
    const hay = cps(args.text(2));
    if (needle.length === 0) fail('E_BAD_ARG', 'REPLACE needle must not be empty', args.posOf(0));
    const out = [];
    let i = 0;
    for (;;) {
      const at = indexOfCp(hay, needle, i);
      if (at < 0) break;
      for (let k = i; k < at; k++) out.push(hay[k]);
      out.push(...repl);
      i = at + needle.length;
    }
    for (let k = i; k < hay.length; k++) out.push(hay[k]);
    return Value.text(fromCodePoints(out));
  },
});

define({
  name: 'SPLIT', min: 2, max: 2,
  fn: (args) => {
    const hay = cps(args.text(0));
    const sep = cps(args.text(1));
    if (sep.length === 0) fail('E_BAD_ARG', 'SPLIT separator must not be empty', args.posOf(1));
    const parts = [];
    let i = 0;
    for (;;) {
      const at = indexOfCp(hay, sep, i);
      if (at < 0) break;
      parts.push(Value.text(fromCodePoints(hay.slice(i, at))));
      i = at + sep.length;
    }
    parts.push(Value.text(fromCodePoints(hay.slice(i))));
    return Value.list(parts);
  },
});

const SPACE = new Set([0x20, 0x09, 0x0d, 0x0a]);

function trim(s, left, right) {
  const c = cps(s);
  let a = 0, b = c.length;
  if (left) while (a < b && SPACE.has(c[a])) a++;
  if (right) while (b > a && SPACE.has(c[b - 1])) b--;
  return fromCodePoints(c.slice(a, b));
}

define({ name: 'TRIM', min: 1, max: 1, fn: (a) => Value.text(trim(a.text(0), true, true)) });
define({ name: 'LTRIM', min: 1, max: 1, fn: (a) => Value.text(trim(a.text(0), true, false)) });
define({ name: 'RTRIM', min: 1, max: 1, fn: (a) => Value.text(trim(a.text(0), false, true)) });

// ASCII only, deliberately. PHP's strtoupper is byte- and locale-based while JS's
// toUpperCase applies full Unicode mapping; they cannot be reconciled without
// shipping a case table, and guessing would break the invariant silently.
function asciiCase(s, up) {
  return fromCodePoints(cps(s).map((c) => {
    if (up && c >= 0x61 && c <= 0x7a) return c - 32;
    if (!up && c >= 0x41 && c <= 0x5a) return c + 32;
    return c;
  }));
}

define({ name: 'UPPER', min: 1, max: 1, fn: (a) => Value.text(asciiCase(a.text(0), true)) });
define({ name: 'LOWER', min: 1, max: 1, fn: (a) => Value.text(asciiCase(a.text(0), false)) });

define({
  name: 'BACKWARDS', min: 1, max: 1,
  fn: (args) => Value.text(fromCodePoints(cps(args.text(0)).reverse())),
});

define({
  name: 'REPEAT', min: 2, max: 2,
  fn: (args) => Value.text(args.text(0).repeat(args.nonNegInt(1))),
});

function pad(args, left) {
  const c = cps(args.text(0));
  const width = args.nonNegInt(1);
  const fill = cps(args.text(2));
  if (fill.length === 0) fail('E_BAD_ARG', 'pad fill must not be empty', args.posOf(2));
  if (c.length >= width) return Value.text(fromCodePoints(c));
  const need = width - c.length;
  const padding = [];
  while (padding.length < need) padding.push(fill[padding.length % fill.length]);
  return Value.text(fromCodePoints(left ? padding.concat(c) : c.concat(padding)));
}

define({ name: 'PADL', min: 3, max: 3, fn: (a) => pad(a, true) });
define({ name: 'PADR', min: 3, max: 3, fn: (a) => pad(a, false) });

define({
  name: 'CHAR', min: 1, max: 1,
  fn: (args) => {
    const n = args.int(0);
    if (n < 0 || n > 0x10ffff || (n >= 0xd800 && n <= 0xdfff)) {
      fail('E_RANGE', `${n} is not an encodable code point`, args.posOf(0));
    }
    return Value.text(fromCodePoints([n]));
  },
});

define({
  name: 'CODE', min: 1, max: 1,
  fn: (args) => {
    const c = cps(args.text(0));
    if (c.length === 0) fail('E_RANGE', 'CODE of empty text', args.posOf(0));
    return Value.int(c[0]);
  },
});
