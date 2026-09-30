// JS-P12: the append idiom A = (A, x) copies the whole list (twice) per append.
import { sel, growth, bench } from './lib.mjs';
const prog = (n) => sel.compile(`A = (1, 2); B = MAP(RANGE_N, 0); ${'A = (A, 7);'.repeat(0)}`);
const mk = (n) => { const p = sel.compile('A = (1, 2); C = 0; MAP(IDX, (A = (A, _); _)); COUNT(A)'); const idx = Array.from({ length: n }, (_, i) => i); return () => p.run({ IDX: idx }).scalar; };
growth('P12 append loop', mk, [1000, 2000, 4000], { reps: 3, warm: 1 });
bench('P12 small append x50000 (ordinary)', () => { const p = sel.compile('A = (1, 2, 3); A = (A, 4); COUNT(A)'); let n = 0; for (let i = 0; i < 50000; i++) n += Number(p.run({}).scalar); return n; }, { reps: 5, warm: 1 });
