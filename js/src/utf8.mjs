// UTF-8 codec, hand-written on purpose.
//
// The host's own facilities are not used: TextDecoder is lenient (it substitutes
// U+FFFD where the spec demands E_UTF8), and JS string indexing counts UTF-16
// units, which disagrees with PHP on every code point above U+FFFF. Every length,
// offset and slice in SEL counts code points, and that has to be true in every
// host or nothing else is.

import { fail } from './errors.mjs';

// --- source positions ---------------------------------------------------------

// Passed as `pos` where the text being decoded IS the program source: an invalid
// unit is then reported where it stands (SPEC §2), counted in code points of the
// valid prefix, rather than with no position at all.
export const SOURCE = Symbol('source');

// The line, column and offset of the code point that would come next after `cps`
// (the code points decoded so far), all in code points, 1-based line and column.
function positionAfter(cps) {
  let line = 1, lineStart = 0;
  for (let i = 0; i < cps.length; i++) {
    if (cps[i] === 10) { line++; lineStart = i + 1; }
  }
  return { line, col: cps.length - lineStart + 1, offset: cps.length };
}

function at(pos, cps) {
  return pos === SOURCE ? positionAfter(cps) : pos;
}

// --- code points ------------------------------------------------------------

// A JS string with an unpaired surrogate has no UTF-8 encoding, so it cannot be
// a SEL TEXT value.
export function toCodePoints(str, pos) {
  const out = [];
  for (let i = 0; i < str.length; i++) {
    const c = str.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = i + 1 < str.length ? str.charCodeAt(i + 1) : 0;
      if (d < 0xdc00 || d > 0xdfff) fail('E_UTF8', 'unpaired high surrogate', at(pos, out));
      out.push(0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00));
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      fail('E_UTF8', 'unpaired low surrogate', at(pos, out));
    } else {
      out.push(c);
    }
  }
  return out;
}

export function fromCodePoints(cps) {
  // Short arrays are the common case and need no slice.
  const n = cps.length;
  if (n === 0) return '';
  if (n <= 4096) return String.fromCodePoint.apply(null, cps);
  let out = '';
  // Chunked to stay clear of argument-count limits on long strings.
  for (let i = 0; i < cps.length; i += 4096) {
    out += String.fromCodePoint.apply(null, cps.slice(i, i + 4096));
  }
  return out;
}

// Any UTF-16 surrogate unit. A string without one has one unit per code point,
// so its native length, positions and order are SEL's; one with them is the slow
// path everywhere (and, at the host boundary, checked for an unpaired one).
export const ANY_SURROGATE = /[\uD800-\uDFFF]/;

// The code point index of UTF-16 offset `u` in `s` (for engine offsets: indexOf,
// regex match positions).
export function cpIndex(s, u) {
  let count = 0;
  let i = 0;
  while (i < u) {
    const c = s.charCodeAt(i);
    i += (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) ? 2 : 1;
    count++;
  }
  return count;
}

// --- bytes ------------------------------------------------------------------

// The UTF-8 length of one code point (SEL text has no lone surrogates).
export function cpUtf8Length(cp) {
  return cp < 0x80 ? 1 : cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4;
}

export function encodeUtf8(str, pos) {
  // Two passes over the code units: the first measures the result and finds an unpaired
  // surrogate (handed to toCodePoints, which raises E_UTF8 with the same message and
  // position as ever), the second writes into a buffer of exactly that size. This was a
  // code point array, an array of bytes and a copy of it.
  const n = str.length;
  let size = 0;
  for (let i = 0; i < n; i++) {
    const c = str.charCodeAt(i);
    if (c < 0x80) size += 1;
    else if (c < 0x800) size += 2;
    else if (c < 0xd800 || c > 0xdfff) size += 3;
    else {
      const d = c <= 0xdbff && i + 1 < n ? str.charCodeAt(i + 1) : 0;
      if (d < 0xdc00 || d > 0xdfff) {
        toCodePoints(str, pos);          // raises the E_UTF8 for the unpaired surrogate
        throw new Error('unreachable');
      }
      size += 4;
      i++;
    }
  }
  const out = new Uint8Array(size);
  if (size === n) {                      // all ASCII
    for (let i = 0; i < n; i++) out[i] = str.charCodeAt(i);
    return out;
  }
  let k = 0;
  for (let i = 0; i < n; i++) {
    const c = str.charCodeAt(i);
    if (c < 0x80) {
      out[k++] = c;
    } else if (c < 0x800) {
      out[k++] = 0xc0 | (c >> 6);
      out[k++] = 0x80 | (c & 0x3f);
    } else if (c < 0xd800 || c > 0xdfff) {
      out[k++] = 0xe0 | (c >> 12);
      out[k++] = 0x80 | ((c >> 6) & 0x3f);
      out[k++] = 0x80 | (c & 0x3f);
    } else {
      const cp = 0x10000 + ((c - 0xd800) << 10) + (str.charCodeAt(++i) - 0xdc00);
      out[k++] = 0xf0 | (cp >> 18);
      out[k++] = 0x80 | ((cp >> 12) & 0x3f);
      out[k++] = 0x80 | ((cp >> 6) & 0x3f);
      out[k++] = 0x80 | (cp & 0x3f);
    }
  }
  return out;
}

// The bytes of a source file as source text: strict, no replacement character,
// no newline translation, and an invalid unit is E_UTF8 at its position.
export function decodeSource(bytes) {
  return decodeUtf8(bytes, SOURCE);
}

// Strict: rejects overlong forms, surrogates, values above U+10FFFF and
// truncated sequences. No replacement characters, ever.
export function decodeUtf8(bytes, pos) {
  // Code units are collected and turned into a string in blocks; the code points decoded
  // so far are only needed to place an error when the text being decoded is the program
  // source, and are rebuilt from the output then.
  let flushed = '';
  const units = [];
  const n = bytes.length;
  const soFar = () => (pos === SOURCE
    ? toCodePoints(flushed + String.fromCharCode.apply(null, units), null) : null);
  let i = 0;
  while (i < n) {
    const b = bytes[i];
    if (b < 0x80) {
      units.push(b);
      i++;
      if (units.length >= 8192) { flushed += String.fromCharCode.apply(null, units); units.length = 0; }
      continue;
    }
    let need, cp, lo, hi;
    if (b >= 0xc2 && b <= 0xdf) {
      need = 1; cp = b & 0x1f; lo = 0x80; hi = 0xbf;
    } else if (b === 0xe0) {
      need = 2; cp = 0; lo = 0xa0; hi = 0xbf;      // reject overlong 3-byte
    } else if (b >= 0xe1 && b <= 0xec) {
      need = 2; cp = b & 0x0f; lo = 0x80; hi = 0xbf;
    } else if (b === 0xed) {
      need = 2; cp = 0x0d; lo = 0x80; hi = 0x9f;   // reject surrogates
    } else if (b >= 0xee && b <= 0xef) {
      need = 2; cp = b & 0x0f; lo = 0x80; hi = 0xbf;
    } else if (b === 0xf0) {
      need = 3; cp = 0; lo = 0x90; hi = 0xbf;      // reject overlong 4-byte
    } else if (b >= 0xf1 && b <= 0xf3) {
      need = 3; cp = b & 0x07; lo = 0x80; hi = 0xbf;
    } else if (b === 0xf4) {
      need = 3; cp = 4; lo = 0x80; hi = 0x8f;      // cap at U+10FFFF
    } else {
      fail('E_UTF8', `invalid start byte 0x${b.toString(16)} at byte ${i}`, at(pos, soFar()));
    }

    if (i + need >= n) {
      fail('E_UTF8', `truncated sequence at byte ${i}`, at(pos, soFar()));
    }
    for (let k = 1; k <= need; k++) {
      const c = bytes[i + k];
      const min = k === 1 ? lo : 0x80;
      const max = k === 1 ? hi : 0xbf;
      if (c < min || c > max) {
        fail('E_UTF8', `invalid continuation byte at byte ${i + k}`, at(pos, soFar()));
      }
      cp = (cp << 6) | (c & 0x3f);
    }
    if (cp >= 0x10000) {
      const v = cp - 0x10000;
      units.push(0xd800 + (v >> 10), 0xdc00 + (v & 0x3ff));
    } else {
      units.push(cp);
    }
    i += need + 1;
  }
  return flushed + String.fromCharCode.apply(null, units);
}

const HEX_PAIRS = Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));

export function bytesToHex(bytes) {
  let out = '';
  for (let i = 0; i < bytes.length; i++) out += HEX_PAIRS[bytes[i]];
  return out;
}

export function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

// Text order is UTF-8 byte order, which is code point order. UTF-16 unit order is the
// same order except where a surrogate meets U+E000..U+FFFF, so two strings with no
// surrogate in either compare natively (a fast path the engine optimises), and
// anything else compares as encoded bytes.
export function compareText(a, b) {
  if (!ANY_SURROGATE.test(a) && !ANY_SURROGATE.test(b)) return a < b ? -1 : a > b ? 1 : 0;
  return bytesCompare(encodeUtf8(a, null), encodeUtf8(b, null));
}

// Bytewise, as the spec requires — JS's native comparison is UTF-16 order and
// disagrees above U+FFFF.
export function bytesCompare(a, b) {
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  }
  return a.length === b.length ? 0 : (a.length < b.length ? -1 : 1);
}
