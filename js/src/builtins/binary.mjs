import { fail } from '../errors.mjs';
import { Value } from '../value.mjs';
import { define } from '../registry.mjs';
import { bytesToHex, decodeUtf8 } from '../utf8.mjs';

define({ name: 'BLEN', min: 1, max: 1, fn: (a) => Value.int(a.bytes(0).length) });
define({ name: 'TO_UTF8', min: 1, max: 1, fn: (a) => Value.bin(a.bytes(0)) });

define({
  name: 'FROM_UTF8', min: 1, max: 1,
  fn: (a) => Value.text(decodeUtf8(a.bytes(0), a.posOf(0))),
});

define({ name: 'TO_HEX', min: 1, max: 1, fn: (a) => Value.text(bytesToHex(a.bytes(0))) });

define({
  name: 'FROM_HEX', min: 1, max: 1,
  fn: (args) => {
    const s = args.text(0);
    if (s.length % 2 !== 0) fail('E_BAD_ARG', 'FROM_HEX needs an even number of digits', args.posOf(0));
    const out = new Uint8Array(s.length / 2);
    for (let i = 0; i < out.length; i++) {
      const pair = s.slice(i * 2, i * 2 + 2);
      if (!/^[0-9a-fA-F]{2}$/.test(pair)) {
        fail('E_BAD_ARG', `FROM_HEX: ${JSON.stringify(pair)} is not hex`, args.posOf(0));
      }
      out[i] = parseInt(pair, 16);
    }
    return Value.bin(out);
  },
});

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const B64_INDEX = (() => {
  const m = new Map();
  for (let i = 0; i < B64.length; i++) m.set(B64[i], i);
  return m;
})();

define({
  name: 'ENCODE_BASE64', min: 1, max: 1,
  fn: (args) => {
    const b = args.bytes(0);
    let out = '';
    for (let i = 0; i < b.length; i += 3) {
      const n = (b[i] << 16) | ((i + 1 < b.length ? b[i + 1] : 0) << 8) | (i + 2 < b.length ? b[i + 2] : 0);
      out += B64[(n >> 18) & 63] + B64[(n >> 12) & 63];
      out += i + 1 < b.length ? B64[(n >> 6) & 63] : '=';
      out += i + 2 < b.length ? B64[n & 63] : '=';
    }
    return Value.text(out);
  },
});

// Strict: padding is required and any character outside the alphabet fails.
define({
  name: 'DECODE_BASE64', min: 1, max: 1,
  fn: (args) => {
    const s = args.text(0);
    const pos = args.posOf(0);
    if (s.length % 4 !== 0) fail('E_BAD_ARG', 'DECODE_BASE64 needs a length that is a multiple of 4', pos);
    const out = [];
    for (let i = 0; i < s.length; i += 4) {
      const quad = [];
      let padding = 0;
      for (let k = 0; k < 4; k++) {
        const ch = s[i + k];
        if (ch === '=') {
          if (i + 4 < s.length || k < 2) fail('E_BAD_ARG', 'misplaced base64 padding', pos);
          padding++;
          quad.push(0);
          continue;
        }
        if (padding > 0) fail('E_BAD_ARG', 'misplaced base64 padding', pos);
        const v = B64_INDEX.get(ch);
        if (v === undefined) fail('E_BAD_ARG', `invalid base64 character ${JSON.stringify(ch)}`, pos);
        quad.push(v);
      }
      const n = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3];
      out.push((n >> 16) & 255);
      if (padding < 2) out.push((n >> 8) & 255);
      if (padding < 1) out.push(n & 255);
    }
    return Value.bin(Uint8Array.from(out));
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
    return Value.text(((crc ^ -1) >>> 0).toString(16).padStart(8, '0'));
  },
});

define({
  name: 'BTL', min: 1, max: 1,
  fn: (args) => Value.list(Array.from(args.bytes(0)).map((b) => Value.int(b))),
});

define({
  name: 'LTB', min: 1, max: 1,
  fn: (args) => {
    const v = args.val(0);
    const items = v.size > 0 ? v.values() : [v];
    const out = new Uint8Array(items.length);
    items.forEach((item, i) => {
      const d = item.asDecimal(args.posOf(0));
      const n = Number(d.digits) * (d.neg ? -1 : 1);
      if (d.scale !== 0 || n < 0 || n > 255) {
        fail('E_RANGE', `LTB element ${i + 1} is not a byte value`, args.posOf(0));
      }
      out[i] = n;
    });
    return Value.bin(out);
  },
});
