import { sel, bench } from './lib.mjs';
const rows = Array.from({ length: 50000 }, (_, i) => ({ a: i % 97, b: (i % 5) - 2, c: i % 1000 }));
const src = 'SUM(ROWS, _["a"]*3 + _["b"]*5 - _["c"]*7 + _["a"]*_["b"] - _["c"]/3 + (_["a"]+_["b"])*(_["c"]-_["a"]) + IF(_["b"] > 0, _["c"], 2) * 4 - _["a"]*_["a"] + _["b"]*_["c"])';
const p = sel.compile(src);
bench('P11 heavy arithmetic + IF, 50k rows', () => p.run({ ROWS: rows }).scalar, { reps: 9, warm: 2 });
