// JS-P4: the SQL translator must not re-evaluate constant subtrees at every level.
import { sel, bench, growth, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const { Sql, Binding } = await import(pathToFileURL(root + '/js/src/sql/index.mjs').href);
const bindings = {
  T: Binding.relation('t', 't', { N: Binding.column('n', 't', 'NUM') }, null, null),
};
const terms = (k) => Array.from({ length: k }, () => 'LEN(REPEAT("x",20000))').join(' + ');
const make = (k) => {
  const p = sel.compile(`T .> FILTER(_["N"] > ${terms(k)})`);
  return () => Sql.translate(p, 'mariadb', bindings).asString?.() ?? String(Sql.translate(p, 'mariadb', bindings)).length;
};
growth('P4 translate FILTER(N > k x LEN(REPEAT(x,20000)))', make, [10, 20, 40, 80], { reps: 3, warm: 1 });
const chain = Array.from({ length: 190 }, () => '1').join(' + ');
const pc = sel.compile(`T .> FILTER(_["N"] > 0 AND ${chain} > 0)`);
bench('P4 190-term constant chain beside a column', () => String(Sql.translate(pc, 'mariadb', bindings)).length, { reps: 5 });
const pn = sel.compile(`T .> FILTER(_["N"] > 3 AND _["N"] < 9)`);
bench('P4 ordinary FILTER translate x2000', () => { let n = 0; for (let i = 0; i < 2000; i++) n += String(Sql.translate(pn, 'mariadb', bindings)).length; return n; }, { reps: 5 });
