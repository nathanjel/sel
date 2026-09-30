// JS-P15: `X IN L` over a value-binding list of N strings must translate in linear time.
import { sel, growth, bench, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const { Sql, Binding } = await import(pathToFileURL(root + '/js/src/sql/index.mjs').href);
const mk = (n) => {
  const list = Array.from({ length: n }, (_, i) => 'v' + i);
  const bindings = { X: Binding.column('x', 't', 'TEXT'), L: Binding.value(sel.Value.fromNative(list)) };
  const p = sel.compile('X IN L');
  return () => String(Sql.translate(p, 'mariadb', bindings)).length;
};
growth('P15 translate X IN L', mk, [2000, 4000, 8000, 16000], { reps: 3, warm: 1 });
const small = mk(5);
bench('P15 5-element IN x20000 (ordinary)', () => { let n = 0; for (let i = 0; i < 20000; i++) n += small(); return n; }, { reps: 5, warm: 1 });
