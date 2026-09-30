// Text built-ins. Everything counts code points — never bytes, never UTF-16
// units — so positions and lengths agree with PHP on astral characters.
// Positions are 1-based and 0 means "not found" (§7.5).

import { fail } from '../errors.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';
import { toCodePoints, fromCodePoints } from '../utf8.mjs';
import { cpLength, checkText, checkCollection } from '../budget.mjs';

const cps = (s) => toCodePoints(s, null);

// A text with no surrogate has one UTF-16 unit per code point, so every position, length
// and slice below is the native one; the code point arrays are only for text that has an
// astral character (JS-P5). A Value's text is well formed, so a surrogate in it is half of
// a valid pair.
const ANY_SURROGATE = /[\uD800-\uDFFF]/;
const plain = (s) => !ANY_SURROGATE.test(s);

// The UTF-16 offset of the code point at index `cp` (or the end when past it).
function unitOffset(s, cp) {
  let i = 0;
  for (let k = 0; k < cp && i < s.length; k++) {
    const c = s.charCodeAt(i);
    i += c >= 0xd800 && c <= 0xdbff ? 2 : 1;
  }
  return i;
}

// The code point index of the UTF-16 offset `u`.
function cpIndex(s, u) {
  let n = 0;
  for (let i = 0; i < u; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) i++;
    n++;
  }
  return n;
}

define({ name: 'LEN', min: 1, max: 1, fn: (args) => Value.int(cpLength(args.text(0))) });

define({
  name: 'LEFT', min: 2, max: 2,
  fn: (args) => {
    const s = args.text(0);
    const n = args.nonNegInt(1);
    if (plain(s)) return Value.textOwned(s.slice(0, n));
    return Value.textOwned(fromCodePoints(cps(s).slice(0, n)));
  },
});

define({
  name: 'RIGHT', min: 2, max: 2,
  fn: (args) => {
    const s = args.text(0);
    const n = args.nonNegInt(1);
    if (plain(s)) return Value.textOwned(s.slice(Math.max(0, s.length - n)));
    const c = cps(s);
    return Value.textOwned(fromCodePoints(c.slice(Math.max(0, c.length - n))));
  },
});

define({
  name: 'SUBSTR', min: 2, max: 3,
  fn: (args) => {
    const s = args.text(0);
    const start = args.int(1);
    if (start < 1) fail('E_RANGE', 'SUBSTR start is 1-based and must be at least 1', args.posOf(1));
    const from = start - 1;
    const count = args.count() === 2 ? null : args.nonNegInt(2);
    if (plain(s)) return Value.textOwned(count === null ? s.slice(from) : s.slice(from, from + count));
    const c = cps(s);
    return Value.textOwned(fromCodePoints(count === null ? c.slice(from) : c.slice(from, from + count)));
  },
});

define({
  name: 'FIND', min: 2, max: 3,
  fn: (args) => {
    const needle = args.text(0);
    const hay = args.text(1);
    let from = 0;
    if (args.count() === 3) {
      const f = args.int(2);
      if (f < 1) fail('E_RANGE', 'FIND start is 1-based and must be at least 1', args.posOf(2));
      from = f - 1;
    }
    if (needle === '') fail('E_BAD_ARG', 'FIND needle must not be empty', args.posOf(0));
    // The engine's indexOf is exact on well-formed UTF-16: a needle cannot begin with half
    // of a pair, so it can only match at a code point boundary (JS-P6, the old loop was
    // O(n*m) over number arrays). Only a haystack with astral characters needs its offsets
    // converted, once each way.
    if (plain(hay)) return Value.int(hay.indexOf(needle, from) + 1);
    const at = hay.indexOf(needle, unitOffset(hay, from));
    return Value.int(at < 0 ? 0 : cpIndex(hay, at) + 1);
  },
});

define({
  name: 'REPLACE', min: 3, max: 3,
  fn: (args) => {
    const needle = args.text(0);
    const repl = args.text(1);
    const hay = args.text(2);
    if (needle === '') fail('E_BAD_ARG', 'REPLACE needle must not be empty', args.posOf(0));
    // Well-formed UTF-16 is searched by code units without ever matching half
    // of a pair, so the string forms are exact; the result length is known from
    // the piece count before the result is built (SPEC 6.4), and nothing here
    // spreads a code point array into an argument list.
    const pieces = hay.split(needle);
    const matches = pieces.length - 1;
    if (matches > 0) {
      const size = cpLength(hay) + matches * (cpLength(repl) - cpLength(needle));
      checkText(size, args.pos, 'REPLACE result');
    }
    return Value.textOwned(pieces.join(repl));
  },
});

define({
  name: 'SPLIT', min: 2, max: 2,
  fn: (args) => {
    const hay = args.text(0);
    const sep = args.text(1);
    if (sep === '') fail('E_BAD_ARG', 'SPLIT separator must not be empty', args.posOf(1));
    const pieces = hay.split(sep);
    checkCollection(pieces.length, args.pos, 'SPLIT result');
    return Value.list(pieces.map((piece) => Value.textOwned(piece)));
  },
});

// The four whitespace characters are ASCII units, and no surrogate unit equals one, so the
// scan is over code units whatever the text holds.
function isSpace(c) { return c === 0x20 || c === 0x09 || c === 0x0d || c === 0x0a; }

function trim(s, left, right) {
  let a = 0, b = s.length;
  if (left) while (a < b && isSpace(s.charCodeAt(a))) a++;
  if (right) while (b > a && isSpace(s.charCodeAt(b - 1))) b--;
  return a === 0 && b === s.length ? s : s.slice(a, b);
}

define({ name: 'TRIM', min: 1, max: 1, fn: (a) => Value.textOwned(trim(a.text(0), true, true)) });
define({ name: 'LTRIM', min: 1, max: 1, fn: (a) => Value.textOwned(trim(a.text(0), true, false)) });
define({ name: 'RTRIM', min: 1, max: 1, fn: (a) => Value.textOwned(trim(a.text(0), false, true)) });

// ASCII only, deliberately. PHP's strtoupper is byte- and locale-based while JS's
// toUpperCase applies full Unicode mapping; they cannot be reconciled without
// shipping a case table, and guessing would break the invariant silently.
function asciiCase(s, up) {
  // Only a-z (or A-Z) move, and they are single units, so the astral characters and every
  // other non-ASCII unit pass through untouched.
  return up ? s.replace(/[a-z]+/g, (m) => m.toUpperCase()) : s.replace(/[A-Z]+/g, (m) => m.toLowerCase());
}

define({ name: 'UPPER', min: 1, max: 1, fn: (a) => Value.textOwned(asciiCase(a.text(0), true)) });
define({ name: 'LOWER', min: 1, max: 1, fn: (a) => Value.textOwned(asciiCase(a.text(0), false)) });

define({
  name: 'BACKWARDS', min: 1, max: 1,
  fn: (args) => {
    const s = args.text(0);
    if (plain(s)) return Value.textOwned(s.split('').reverse().join(''));
    return Value.textOwned(fromCodePoints(cps(s).reverse()));
  },
});

define({
  name: 'REPEAT', min: 2, max: 2,
  fn: (args) => {
    const text = args.text(0);
    const count = args.nonNegInt(1);
    // An empty text repeated any number of times is empty, and a count of zero
    // is empty text of any length (SPEC 6.4: an empty result is never too large).
    if (text === '' || count === 0) return Value.textOwned('');
    checkText(cpLength(text) * count, args.pos, 'REPEAT result');
    return Value.textOwned(text.repeat(count));
  },
});

function pad(args, left) {
  const text = args.text(0);
  const width = args.nonNegInt(1);
  const fill = cps(args.text(2));
  if (fill.length === 0) fail('E_BAD_ARG', 'pad fill must not be empty', args.posOf(2));
  const len = cpLength(text);
  if (len >= width) return Value.textOwned(text);
  checkText(width, args.pos, 'PAD result');
  const need = width - len;
  // Whole cycles of the fill, then the part of one that fits: an astral fill
  // is cut between code points, never inside one.
  const fillText = fromCodePoints(fill);
  const padding = fillText.repeat(Math.floor(need / fill.length))
    + fromCodePoints(fill.slice(0, need % fill.length));
  return Value.textOwned(left ? padding + text : text + padding);
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
    return Value.textOwned(fromCodePoints([n]));
  },
});

define({
  name: 'CODE', min: 1, max: 1,
  fn: (args) => {
    const s = args.text(0);
    if (s.length === 0) fail('E_RANGE', 'CODE of empty text', args.posOf(0));
    return Value.int(s.codePointAt(0));
  },
});
