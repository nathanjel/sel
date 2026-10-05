import { fail } from '../errors.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';
import { bytesToHex, decodeUtf8 } from '../utf8.mjs';
import { checkText, checkCollection } from '../budget.mjs';
import * as D from '../decimal.mjs';

define({ name: 'BLEN', min: 1, max: 1, fn: (a) => Value.int(a.bytes(0).length) });
define({
  name: 'TO_UTF8', min: 1, max: 1,
  fn: (a) => {
    const b = a.bytes(0);
    checkText(b.length, a.pos, 'TO_UTF8 result');
    return Value.bin(b);
  },
});

define({
  name: 'FROM_UTF8', min: 1, max: 1,
  fn: (a) => Value.text(decodeUtf8(a.bytes(0), a.posOf(0))),
});

define({
  name: 'TO_HEX', min: 1, max: 1,
  fn: (a) => {
    const b = a.bytes(0);
    checkText(b.length * 2, a.pos, 'TO_HEX result');
    return Value.textOwned(bytesToHex(b));
  },
});

// -1 for anything that is not a hex digit, indexed by code unit.
const HEX_VALUE = (() => {
  const t = new Int8Array(128).fill(-1);
  for (let i = 0; i < 10; i++) t[0x30 + i] = i;
  for (let i = 0; i < 6; i++) { t[0x41 + i] = 10 + i; t[0x61 + i] = 10 + i; }
  return t;
})();

define({
  name: 'FROM_HEX', min: 1, max: 1,
  fn: (args) => {
    const s = args.text(0);
    if (s.length % 2 !== 0) fail('E_BAD_ARG', 'FROM_HEX needs an even number of digits', args.posOf(0));
    const out = new Uint8Array(s.length / 2);
    for (let i = 0; i < out.length; i++) {
      const a = s.charCodeAt(i * 2), b = s.charCodeAt(i * 2 + 1);
      const hi = a < 128 ? HEX_VALUE[a] : -1, lo = b < 128 ? HEX_VALUE[b] : -1;
      if (hi < 0 || lo < 0) {
        fail('E_BAD_ARG', `FROM_HEX: ${JSON.stringify(s.slice(i * 2, i * 2 + 2))} is not hex`, args.posOf(0));
      }
      out[i] = (hi << 4) | lo;
    }
    return Value.binOwned(out);
  },
});

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const B64_CODES = Uint8Array.from(B64, (ch) => ch.charCodeAt(0));
// The 6-bit value of a base64 character by code unit, -1 for anything else ('=' included).
const B64_VALUE = (() => {
  const t = new Int8Array(128).fill(-1);
  for (let i = 0; i < B64.length; i++) t[B64.charCodeAt(i)] = i;
  return t;
})();

// A byte array of ASCII codes as a string, in blocks that stay clear of argument limits.
function asciiString(codes) {
  let out = '';
  for (let i = 0; i < codes.length; i += 8192) {
    out += String.fromCharCode.apply(null, codes.subarray(i, i + 8192));
  }
  return out;
}

define({
  name: 'ENCODE_BASE64', min: 1, max: 1,
  fn: (args) => {
    const b = args.bytes(0);
    checkText(4 * Math.ceil(b.length / 3), args.pos, 'ENCODE_BASE64 result');
    const out = new Uint8Array(4 * Math.ceil(b.length / 3));
    let k = 0;
    for (let i = 0; i < b.length; i += 3) {
      const n = (b[i] << 16) | ((i + 1 < b.length ? b[i + 1] : 0) << 8) | (i + 2 < b.length ? b[i + 2] : 0);
      out[k++] = B64_CODES[(n >> 18) & 63];
      out[k++] = B64_CODES[(n >> 12) & 63];
      out[k++] = i + 1 < b.length ? B64_CODES[(n >> 6) & 63] : 0x3d;
      out[k++] = i + 2 < b.length ? B64_CODES[n & 63] : 0x3d;
    }
    return Value.textOwned(asciiString(out));
  },
});

// Strict: padding is required and any character outside the alphabet fails.
define({
  name: 'DECODE_BASE64', min: 1, max: 1,
  fn: (args) => {
    const s = args.text(0);
    const pos = args.posOf(0);
    if (s.length % 4 !== 0) fail('E_BAD_ARG', 'DECODE_BASE64 needs a length that is a multiple of 4', pos);
    const out = new Uint8Array((s.length / 4) * 3);
    let k = 0;
    for (let i = 0; i < s.length; i += 4) {
      let n = 0;
      let padding = 0;
      for (let q = 0; q < 4; q++) {
        const code = s.charCodeAt(i + q);
        if (code === 0x3d) {
          if (i + 4 < s.length || q < 2) fail('E_BAD_ARG', 'misplaced base64 padding', pos);
          padding++;
          n <<= 6;
          continue;
        }
        if (padding > 0) fail('E_BAD_ARG', 'misplaced base64 padding', pos);
        const v = code < 128 ? B64_VALUE[code] : -1;
        if (v < 0) fail('E_BAD_ARG', `invalid base64 character ${JSON.stringify(s[i + q])}`, pos);
        n = (n << 6) | v;
      }
      out[k++] = (n >> 16) & 255;
      if (padding < 2) out[k++] = (n >> 8) & 255;
      if (padding < 1) out[k++] = n & 255;
    }
    return Value.binOwned(k === out.length ? out : out.slice(0, k));
  },
});

// CRC-32/ISO-HDLC: reflected, polynomial 0xEDB88320, init and final xor all ones.
let CRC_TABLE = null;
function crcTable() {
  if (CRC_TABLE) return CRC_TABLE;
  CRC_TABLE = new Int32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    CRC_TABLE[i] = c;
  }
  return CRC_TABLE;
}

define({
  name: 'CRC32', min: 1, max: 1,
  fn: (args) => {
    const t = crcTable();
    const b = args.bytes(0);
    let crc = -1;
    for (let i = 0; i < b.length; i++) crc = t[(crc ^ b[i]) & 255] ^ (crc >>> 8);
    return Value.textOwned(((crc ^ -1) >>> 0).toString(16).padStart(8, '0'));
  },
});

define({
  name: 'BTL', min: 1, max: 1,
  fn: (args) => {
    const b = args.bytes(0);
    checkCollection(b.length, args.pos, 'BTL result');
    const out = new Array(b.length);
    for (let i = 0; i < b.length; i++) out[i] = Value.byteValue(b[i]);
    return Value.listOwned(out);
  },
});

define({
  name: 'LTB', min: 1, max: 1,
  fn: (args) => {
    const v = args.val(0);
    // An empty list (or NULL) is the empty BIN, so LTB(BTL(x)) is x for every
    // BIN x, the empty one included; a scalar is a one-element list.
    if (v.size() === 0 && (v.isList || v.isNull())) return Value.binOwned(new Uint8Array(0));
    const items = v.size() > 0 ? v.values() : [v];
    const out = new Uint8Array(items.length);
    items.forEach((item, i) => {
      // An integral value of any scale is a byte candidate (`1.0`, `"65"`).
      const d = item.asDecimal(args.posOf(0));
      if (!D.isInteger(d)) {
        fail('E_NOT_INT', `LTB element ${i + 1} must be a whole number`, args.posOf(0));
      }
      const n = D.truncToNumber(d);
      if (n < 0 || n > 255) {
        fail('E_RANGE', `LTB element ${i + 1} is not a byte value`, args.posOf(0));
      }
      out[i] = n;
    });
    return Value.binOwned(out);
  },
});
