// Differential for JS-P7: the codecs and the binary builtins against a saved copy of the
// previous tree (SEL_OLD_ROOT=/path containing js/src): random bytes and strings must give
// the same value, or the same error code and position.
import { pathToFileURL } from 'node:url';
import { rng } from './lib.mjs';
const old = await import(pathToFileURL(process.env.SEL_OLD_ROOT + '/js/src/sel.mjs').href);
const nw = await import(pathToFileURL(new URL('../../../js/src/sel.mjs', import.meta.url).pathname).href);
const ou = await import(pathToFileURL(process.env.SEL_OLD_ROOT + '/js/src/utf8.mjs').href);
const nu = await import(pathToFileURL(new URL('../../../js/src/utf8.mjs', import.meta.url).pathname).href);
const r = rng(+process.argv[2] || 11);
const N = +process.argv[3] || 100000;
const ex = (f) => { try { return JSON.stringify(Array.from(f())); } catch (e) { return `ERR ${e.code}@${e.line}:${e.col}:${e.offset}`; } };
const exs = (f) => { try { return String(f()); } catch (e) { return `ERR ${e.code}@${e.line}:${e.col}:${e.offset}`; } };
const pick = (a) => a[(r() * a.length) | 0];
const U = ['a', 'é', 'ł', '日', '😀', '\u{10FFFF}', '\u0000', ' ', '\ud800', '\udc00', 'Z', '߿', 'ࠀ', '￿'];
const B = [0x00, 0x41, 0x7f, 0x80, 0xbf, 0xc0, 0xc1, 0xc2, 0xdf, 0xe0, 0xe1, 0xec, 0xed, 0xee, 0xef, 0xf0, 0xf1, 0xf3, 0xf4, 0xf5, 0xff, 0x9f, 0xa0, 0x8f, 0x90];
let bad = 0;
const report = (what, input, a, b) => { if (a !== b && bad++ < 8) console.log('DIFF', what, JSON.stringify(input).slice(0, 120), '\n ', a.slice(0, 160), '\n ', b.slice(0, 160)); };
for (let k = 0; k < N; k++) {
  let s = ''; for (let i = (r() * 14) | 0; i > 0; i--) s += pick(U);
  report('encodeUtf8', s, ex(() => ou.encodeUtf8(s, null)), ex(() => nu.encodeUtf8(s, null)));
  const bytes = Uint8Array.from({ length: (r() * 14) | 0 }, () => (r() < 0.5 ? pick(B) : (r() * 256) | 0));
  report('decodeUtf8', Array.from(bytes), exs(() => ou.decodeUtf8(bytes, null)), exs(() => nu.decodeUtf8(bytes, null)));
  report('decodeSource', Array.from(bytes), exs(() => ou.decodeSource(bytes)), exs(() => nu.decodeSource(bytes)));
  report('hex', Array.from(bytes), exs(() => ou.bytesToHex(bytes)), exs(() => nu.bytesToHex(bytes)));
  // the builtins, through the language
  const hexish = Array.from({ length: (r() * 10) | 0 }, () => pick('0123456789abcdefABCDEFgG é😀'.split(''))).join('');
  const q = JSON.stringify(hexish);
  const hx = Array.from(bytes, (x) => x.toString(16).padStart(2, '0')).join('');
  const b64 = ((r() * 4) | 0) === 0 ? JSON.stringify(Buffer.from(bytes).toString('base64').replace(/.$/, pick(['A', '=', '*', 'é']))) : JSON.stringify(Buffer.from(bytes).toString('base64'));
  for (const src of [`DECODE_BASE64(${b64})`, `DECODE_BASE64(ENCODE_BASE64(FROM_HEX("${hx}")))`, `FROM_UTF8(FROM_HEX("${hx}"))`, `FROM_HEX(${q})`, `DECODE_BASE64(${q})`, `ENCODE_BASE64(TO_UTF8(${q}))`, `TO_HEX(TO_UTF8(${q}))`, `CRC32(${q})`]) {
    let a, b;
    try { a = old.evaluate(src).dump(); } catch (e) { a = `ERR ${e.code}@${e.line}:${e.col}`; }
    try { b = nw.evaluate(src).dump(); } catch (e) { b = `ERR ${e.code}@${e.line}:${e.col}`; }
    report(src, hexish, a, b);
  }
}
console.log('done', N, 'diffs', bad);
