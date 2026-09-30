import { tokenize as nw } from '../../../js/src/lexer.mjs';
// Differential against a saved copy of the previous lexer (SEL_OLD_LEXER=/path/to/lexer.mjs,
// with its imports made absolute): every random string must give the same token stream or the
// same error code and position.
import { pathToFileURL } from 'node:url';
const { tokenize: od } = await import(pathToFileURL(process.env.SEL_OLD_LEXER).href);
const alpha = ['"','"','{','{','}','}',"'",'\\','#','\n','a','1',' ','(',')','+','-','*','??','???','==','$==','.>','é','😀','@','1.5','_','\r'];
let seed = +process.argv[2] || 1;
const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const run = (f, s) => { try { return JSON.stringify(f(s)); } catch (e) { return 'ERR ' + (e.code||e.name) + ' ' + JSON.stringify([e.line,e.col,e.offset]); } };
let bad = 0, N = +process.argv[3] || 200000;
for (let k = 0; k < N; k++) {
  const len = 1 + Math.floor(rnd() * 30);
  let s = ''; for (let i = 0; i < len; i++) s += alpha[Math.floor(rnd() * alpha.length)];
  const a = run(od, s), b = run(nw, s);
  if (a !== b && bad++ < 5) console.log('DIFF', JSON.stringify(s), '\n', a.slice(0,200), '\n', b.slice(0,200));
}
console.log('done', N, 'diffs', bad);
