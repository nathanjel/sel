// JS-P9: building rows from entries (RECORD / SELECT_COLS / fromEntries) must not rebuild the shape per row.
import { sel, bench, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const { Value } = await import(pathToFileURL(root + '/js/src/value.mjs').href);
const mk = (i) => [['id', Value.int(i)], ['name', Value.text('n' + i)], ['a', Value.int(1)], ['b', Value.int(2)], ['c', Value.int(3)], ['d', Value.int(4)]];
bench('P9 Value.fromEntriesOwned 6 keys x1e6', () => { let n = 0; for (let i = 0; i < 1e6; i++) n += Value.fromEntriesOwned(mk(i)).size(); return n; }, { reps: 5, warm: 1 });
const rows = Array.from({ length: 300000 }, (_, i) => ({ id: i, name: 'n' + i, a: 1, b: 2 }));
bench('P9 MAP(rows, RECORD(...)) 300k', () => sel.compile('COUNT(MAP(ROWS, RECORD("id", _["id"], "x", _["a"] + _["b"], "n", _["name"])))').run({ ROWS: rows }).scalar, { reps: 3, warm: 1 });
bench('P9 SELECT_COLS 300k', () => sel.compile('COUNT(SELECT_COLS(ROWS, "id", "name"))').run({ ROWS: rows }).scalar, { reps: 3, warm: 1 });
