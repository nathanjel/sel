// JS-P19: a numeric join over keys the `==` coercion rejects must not pay a thrown+caught SelError per row.
import { sel, bench } from './lib.mjs';
const mk = (n) => Array.from({ length: n }, (_, i) => ({ k: 'id-' + i, v: i }));
const L = [{ k: 7, v: 1 }];
for (const n of [50000, 100000, 200000]) {
  const R = mk(n);
  const p = sel.compile('COUNT(LINK(L, R, A, B, A["k"] == B["k"]))');
  bench(`P19 numeric-join key probe, ${n} bad-key rows`, () => { try { return p.run({ L, R }).scalar; } catch (e) { return e.code + '@' + e.line + ':' + e.col; } }, { reps: 5, warm: 1 });
}
const R2 = Array.from({ length: 200000 }, (_, i) => ({ k: String(i), v: i }));
const q = sel.compile('COUNT(LINK(L, R, A, B, A["k"] == B["k"]))');
bench('P19 ordinary numeric-text join 200k (control)', () => q.run({ L: [{ k: 7 }, { k: 9 }], R: R2 }).scalar, { reps: 5, warm: 1 });
