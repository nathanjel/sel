// JS-P1: repeated `&`/`&=` must stay linear (the surrogate scan flattened V8 ropes).
import { sel, bench, growth } from './lib.mjs';
const make = (n) => {
  const p = sel.compile('L = SPLIT(REPEAT("a,", N), ","); S = ""; ALL(L, IS_NULL(S &= "abcdefghij") OR TRUE); LEN(S)'.replace('N', String(n)));
  return () => p.run({}).scalar;
};
growth('P1 S &= "abcdefghij" over SPLIT(REPEAT(a,,n))', make, [10000, 20000, 40000], { reps: 5, warm: 1 });
const small = sel.compile('S = "ab"; T = S & "cd" & "é"; LEN(T)');
bench('P1 tiny program (concat of 3 short texts)', () => { let x; for (let i = 0; i < 20000; i++) x = small.run({}).scalar; return x; }, { reps: 7 });
