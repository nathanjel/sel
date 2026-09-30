// JS-P3: equi join over wide rows (shape interning cliff past ~254 fields).
import { sel, bench } from './lib.mjs';
function rows(n, c, off) {
  const out = [];
  for (let i = 0; i < n; i++) { const r = { k: String(i + off) }; for (let j = 0; j < c; j++) r['c' + j] = String(i * 7 + j); out.push(r); }
  return out;
}
const prog = sel.compile('COUNT(LINK(L, R, l, r, l["k"] == r["k"]))');
for (const c of [100, 250, 260, 300, 500]) {
  const ctx = { L: rows(3000, c, 0), R: rows(3000, c, 0) };
  bench(`P3 equi join 3000x3000 C=${c}`, () => prog.run(ctx).scalar, { reps: 3, warm: 1 });
}
const small = { L: rows(3000, 6, 0), R: rows(3000, 6, 0) };
bench('P3 ordinary rows C=6 (overhead control)', () => prog.run(small).scalar, { reps: 5 });
