// JS-P22: a non-equi LINK re-aliased the right row for every pair.
import { sel, bench, rng } from './lib.mjs';
const r = rng(22);
const mk = (n, name) => Array.from({ length: n }, (_, i) => ({ id: i, k: Math.floor(r() * 50), v: Math.floor(r() * 1000), [name]: 'x' + i }));
const N = Number(process.env.P22_N || 600);
const L = mk(N, 'l'), R = mk(N, 'r');
const p = sel.compile('COUNT(LINK(L, R, a, b, a["v"] < b["v"] AND a["k"] == b["k"] + 0))');
const q = sel.compile('COUNT(LINK(L, R, a, b, a["v"] < b["v"] AND a["k"] != b["k"]))');
bench(`P22 non-equi LINK ${N}x${N} (equality + residual, nested loop)`, () => p.run({ L, R }).scalar, { reps: 7, warm: 2 });
bench(`P22 non-equi LINK ${N}x${N} (no equality)`, () => q.run({ L, R }).scalar, { reps: 5, warm: 1 });
for (const n of [300, 600, 1200]) {
  const l = mk(n, 'l'), rr = mk(n, 'r');
  bench(`P22 growth n=${n}`, () => q.run({ L: l, R: rr }).scalar, { reps: 3, warm: 1 });
}
