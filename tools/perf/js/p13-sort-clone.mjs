// JS-P13: SORT_BY deep-copies every result element. SPEC 3.4 (and conformance alias.aggregate-copies.sort*) requires the copy.
import { sel, bench } from './lib.mjs';
const rows = Array.from({ length: 100000 }, (_, i) => ({ id: i, k: (i * 7919) % 100003, nest: { a: i, l: [1, 2, 3] } }));
const p = sel.compile('COUNT(SORT_BY(ROWS, _["k"]))');
bench('P13 SORT_BY 100k nested rows', () => p.run({ ROWS: rows }).scalar, { reps: 5, warm: 1 });
