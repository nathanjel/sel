// JS-P2: lexer throughput on a 2 MB source (about 720k tokens) and on small rules.
import { bench, rng, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const { tokenize } = await import(pathToFileURL(root + '/js/src/lexer.mjs').href);
const r = rng(7);
const names = ['TOTAL', 'CREDIT_LIMIT', 'ITEMS', 'A', 'B1', 'price'];
function source(bytes) {
  const out = [];
  let n = 0;
  while (n < bytes) {
    const t = `${names[(r() * names.length) | 0]}[${(r() * 100) | 0}] ${['+', '-', '*', '>=', '==', '??', '&'][(r() * 7) | 0]} ${(r() * 1000).toFixed(2)} ; "s{A}t" # c\n`;
    out.push(t); n += t.length;
  }
  return out.join('');
}
const big = source(2_000_000);
const small = 'TOTAL > CREDIT_LIMIT AND COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I["qty"] > 0)';
const digest = (toks) => { let h = 0; for (const t of toks) h = (h * 31 + t.value.length + t.line * 7 + t.col * 13 + t.offset) | 0; return toks.length + ':' + h; };
bench('P2 tokenize 2 MB', () => digest(tokenize(big)), { reps: 5, warm: 1 });
bench('P2 tokenize small rule x20000', () => { let d; for (let i = 0; i < 20000; i++) d = tokenize(small).length; return d; }, { reps: 7 });
