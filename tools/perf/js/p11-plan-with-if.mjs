// JS-P11: one non-plannable operand (IF/COND/`,`/`;`/assignment) must not disable the math plan for the rest of the arithmetic.
import { sel, bench, growth, rng } from './lib.mjs';
const r = rng(11);
const mk = (n) => Array.from({ length: n }, (_, i) => ({ a: i % 97, b: (i % 5) - 2, c: Math.floor(r() * 1000) }));
const progs = {
  'IF operand': 'SUM(ROWS, _["a"] * 3 + IF(_["b"] > 0, _["c"], 2) - _["a"] / 4)',
  'COND operand': 'SUM(ROWS, (_["a"] + 1) * COND(_["b"] > 1, 5, _["b"] < 0, 7, 9) + _["c"])',
  'plain (control)': 'SUM(ROWS, _["a"] * 3 + _["c"] - _["a"] / 4)',
};
for (const [name, src] of Object.entries(progs)) {
  const p = sel.compile(src);
  for (const n of [25000, 50000, 100000]) {
    const rows = mk(n);
    bench(`P11 ${name} n=${n}`, () => p.run({ ROWS: rows }).scalar, { reps: 7, warm: 2 });
  }
}
// ordinary tiny workload: compile + run of a small rule
const small = 'IF(A > 1, A * 2 + B, A - B) + A * B';
bench('P11 tiny rule x100000', () => { const p = sel.compile(small); let n = 0; for (let i = 0; i < 100000; i++) n += Number(p.run({ A: i % 7, B: 3 }).scalar); return n; }, { reps: 5, warm: 1 });
