// JS-P14: nodeContainsVar (does the aggregate body read _K?) is asked on every aggregate call.
import { sel, growth, bench } from './lib.mjs';
const mk = (n) => { const rows = Array.from({ length: n }, (_, i) => ({ b: [1, 2, 3].map((x) => ({ b: x + (i % 3) })) }));
  const p = sel.compile('COUNT(MAP(R, SUM(_["b"], _["b"] * 2 + _["b"] - 1 + _["b"] * _["b"] + 3 + _["b"])))'); return () => p.run({ R: rows }).scalar; };
growth('P14 nested aggregate over outer rows', mk, [50000, 100000, 200000], { reps: 5, warm: 1 });
bench('P14 tiny aggregate x100000', () => { const p = sel.compile('SUM(L, _ + 1)'); let n = 0; const L = [1, 2, 3]; for (let i = 0; i < 100000; i++) n += Number(p.run({ L }).scalar); return n; }, { reps: 5, warm: 1 });
