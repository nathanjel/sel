// The two size caps of SPEC §6.4, checked BEFORE anything is allocated.
//
// MAX_TEXT_LEN bounds a TEXT value's code points and a BIN value's bytes that an
// operation may build; MAX_COLLECTION bounds the children of a collection an
// operation may build. Exceeding either is E_RANGE at the node that builds the
// value, after its arguments have been evaluated and coerced. The length is
// worked out from the operands' lengths (a Number, so a count of 10^30 or a
// 400-digit one is simply larger than the cap), and an EMPTY result is never
// too large: `REPEAT("", 10^30)` is "".

import { fail } from './errors.mjs';
import { MAX_TEXT_LEN, MAX_COLLECTION } from './_limits.mjs';

// Code points in a JS string. Text is well formed (E_UTF8 rejects a lone
// surrogate at the boundary), so a pair is exactly one code point.
export function cpLength(s) {
  let n = s.length;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) { n--; i++; }
  }
  return n;
}

export function checkText(len, pos, what = 'result') {
  if (len > MAX_TEXT_LEN) {
    fail('E_RANGE', `${what} would be longer than ${MAX_TEXT_LEN}`, pos);
  }
}

export function checkCollection(n, pos, what = 'collection') {
  if (n > MAX_COLLECTION) {
    fail('E_RANGE', `${what} would have more than ${MAX_COLLECTION} elements`, pos);
  }
}

export { MAX_TEXT_LEN, MAX_COLLECTION };
