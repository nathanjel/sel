// UTF-8 codec, hand-written on purpose.
//
// The host's own facilities are not used: TextDecoder is lenient (it substitutes
// U+FFFD where the spec demands E_UTF8), and JS string indexing counts UTF-16
// units, which disagrees with PHP on every code point above U+FFFF. Every length,
// offset and slice in SEL counts code points, and that has to be true in both
// hosts or nothing else is.

import { fail } from './errors.mjs';

// --- code points ------------------------------------------------------------

// A JS string with an unpaired surrogate has no UTF-8 encoding, so it cannot be
// a SEL TEXT value.
export function toCodePoints(str, pos) {
  const out = [];
  for (let i = 0; i < str.length; i++) {
    const c = str.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const d = i + 1 < str.length ? str.charCodeAt(i + 1) : 0;
      if (d < 0xdc00 || d > 0xdfff) fail('E_UTF8', 'unpaired high surrogate', pos);
      out.push(0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00));
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      fail('E_UTF8', 'unpaired low surrogate', pos);
    } else {
      out.push(c);
    }
  }
  return out;
}

export function fromCodePoints(cps) {
  let out = '';
  // Chunked to stay clear of argument-count limits on long strings.
  for (let i = 0; i < cps.length; i += 4096) {
    out += String.fromCodePoint.apply(null, cps.slice(i, i + 4096));
  }
  return out;
}

export function cpLength(str, pos) {
  return toCodePoints(str, pos).length;
}

// start is 0-based here; the 1-based convention belongs to the built-ins.
export function cpSlice(str, start, len, pos) {
  const cps = toCodePoints(str, pos);
  return fromCodePoints(cps.slice(start, len === undefined ? undefined : start + len));
}

// --- bytes ------------------------------------------------------------------

export function encodeUtf8(str, pos) {
  const cps = toCodePoints(str, pos);
  const out = [];
  for (const c of cps) {
    if (c < 0x80) {
      out.push(c);
    } else if (c < 0x800) {
      out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
    } else if (c < 0x10000) {
      out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    } else {
      out.push(
        0xf0 | (c >> 18),
        0x80 | ((c >> 12) & 0x3f),
        0x80 | ((c >> 6) & 0x3f),
        0x80 | (c & 0x3f),
      );
    }
  }
  return Uint8Array.from(out);
}

// Strict: rejects overlong forms, surrogates, values above U+10FFFF and
// truncated sequences. No replacement characters, ever.
export function decodeUtf8(bytes, pos) {
  const cps = [];
  const n = bytes.length;
  let i = 0;
  while (i < n) {
    const b = bytes[i];
    let need, cp, lo, hi;
    if (b < 0x80) {
      cps.push(b);
      i++;
      continue;
    } else if (b >= 0xc2 && b <= 0xdf) {
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
      fail('E_UTF8', `invalid start byte 0x${b.toString(16)} at byte ${i}`, pos);
    }

    if (i + need >= n) {
      fail('E_UTF8', `truncated sequence at byte ${i}`, pos);
    }
    for (let k = 1; k <= need; k++) {
      const c = bytes[i + k];
      const min = k === 1 ? lo : 0x80;
      const max = k === 1 ? hi : 0xbf;
      if (c < min || c > max) {
        fail('E_UTF8', `invalid continuation byte at byte ${i + k}`, pos);
      }
      cp = (cp << 6) | (c & 0x3f);
    }
    cps.push(cp);
    i += need + 1;
  }
  return fromCodePoints(cps);
}

export function bytesToHex(bytes) {
  let out = '';
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i].toString(16).padStart(2, '0');
  }
  return out;
}

export function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
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
