// JS-P8: text comparison, SORT / SORT_BY / TOP over text and numeric keys.
import { sel, bench, rng } from './lib.mjs';
const r = rng(5);
const N = 200000;
const txt = Array.from({ length: N }, () => 'k' + ((r() * 1e6) | 0));
const num = Array.from({ length: N }, () => String((r() * 1e6) | 0) + (r() < 0.3 ? '.5' : ''));
const recs = Array.from({ length: N }, (_, i) => ({ s: txt[i], n: num[i] }));
const P = (s) => sel.compile(s);
bench('P8 SORT(200k "k123" text)', () => P('COUNT(SORT(L))').run({ L: txt }).scalar, { reps: 3, warm: 1 });
bench('P8 SORT(200k numeric text)', () => P('COUNT(SORT(L))').run({ L: num }).scalar, { reps: 3, warm: 1 });
bench('P8 SORT_BY(rows, _["s"])', () => P('COUNT(SORT_BY(L, _["s"]))').run({ L: recs }).scalar, { reps: 3, warm: 1 });
bench('P8 SORT_BY(rows, _["s"], "DESC")', () => P('COUNT(SORT_BY(L, _["s"], "DESC"))').run({ L: recs }).scalar, { reps: 3, warm: 1 });
bench('P8 TOP_BY(rows, _["n"], 10)', () => P('COUNT(TOP_BY(L, _["n"], 10))').run({ L: recs }).scalar, { reps: 3, warm: 1 });
bench('P8 $< over 200k text pairs', () => P('COUNT(FILTER(L, _ $< "k5"))').run({ L: txt }).scalar, { reps: 3, warm: 1 });
bench('P8 == over 200k numeric text', () => P('COUNT(FILTER(L, _ == 5000))').run({ L: num }).scalar, { reps: 3, warm: 1 });
bench('P8 SORT 20 short rows x5000 (overhead)', () => { const p = P('COUNT(SORT(L))'); let n = 0; const L = txt.slice(0, 20); for (let i = 0; i < 5000; i++) n += +p.run({ L }).scalar; return n; }, { reps: 5, warm: 1 });
