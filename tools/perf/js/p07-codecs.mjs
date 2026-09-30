// JS-P7: UTF-8, hex and base64 codecs.
import { sel, bench, rng, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const u = await import(pathToFileURL(root + '/js/src/utf8.mjs').href);
const r = rng(3);
let mixed = ''; while (mixed.length < 1_600_000) { const x = r(); mixed += x < 0.6 ? 'abc ' : x < 0.85 ? 'zażółć ' : x < 0.97 ? '日本語' : '😀'; }
const ascii = 'abcdefghij'.repeat(200000);
const digest = (b) => { let h = 0; for (let i = 0; i < b.length; i += 97) h = (h * 31 + b[i]) | 0; return b.length + ':' + h; };
bench('P7 encodeUtf8 1.6M mixed', () => digest(u.encodeUtf8(mixed, null)), { reps: 5, warm: 1 });
bench('P7 encodeUtf8 2M ascii', () => digest(u.encodeUtf8(ascii, null)), { reps: 5, warm: 1 });
const bytesMixed = u.encodeUtf8(mixed, null), bytesAscii = u.encodeUtf8(ascii, null);
bench('P7 decodeUtf8 mixed', () => u.decodeUtf8(bytesMixed, null).length, { reps: 5, warm: 1 });
bench('P7 decodeUtf8 2M ascii', () => u.decodeUtf8(bytesAscii, null).length, { reps: 5, warm: 1 });
bench('P7 bytesToHex 2M', () => u.bytesToHex(bytesAscii).length, { reps: 5, warm: 1 });
const P = (s) => sel.compile(s);
const big1m = 'x'.repeat(1000000);
bench('P7 TO_HEX(TO_UTF8(1MB))', () => P('LEN(TO_HEX(TO_UTF8(S)))').run({ S: big1m }).scalar, { reps: 5, warm: 1 });
bench('P7 FROM_HEX(TO_HEX(1MB))', () => P('BLEN(FROM_HEX(H))').run({ H: u.bytesToHex(u.encodeUtf8(big1m, null)) }).scalar, { reps: 5, warm: 1 });
bench('P7 ENCODE_BASE64(TO_UTF8(1MB))', () => P('LEN(ENCODE_BASE64(TO_UTF8(S)))').run({ S: big1m }).scalar, { reps: 5, warm: 1 });
const b64 = P('ENCODE_BASE64(TO_UTF8(S))').run({ S: big1m }).scalar;
bench('P7 DECODE_BASE64(1MB)', () => P('BLEN(DECODE_BASE64(B))').run({ B: b64 }).scalar, { reps: 5, warm: 1 });
bench('P7 CRC32(1MB)', () => P('CRC32(TO_UTF8(S))').run({ S: big1m }).scalar, { reps: 5, warm: 1 });
const rows = Array.from({ length: 300000 }, () => ({ b: 'QUItMTAwMA==' }));
bench('P7 300k rows DECODE_BASE64(small)', () => P('COUNT(MAP(ROWS, BLEN(DECODE_BASE64(_["b"]))))').run({ ROWS: rows }).scalar, { reps: 3, warm: 1 });
bench('P7 11-char strings x1e6 encodeUtf8', () => { let n = 0; for (let i = 0; i < 1e6; i++) n += u.encodeUtf8('hello world', null).length; return n; }, { reps: 3, warm: 1 });
bench('P7 11-byte bytesToHex x1e6', () => { const b = u.encodeUtf8('hello world', null); let n = 0; for (let i = 0; i < 1e6; i++) n += u.bytesToHex(b).length; return n; }, { reps: 3, warm: 1 });
