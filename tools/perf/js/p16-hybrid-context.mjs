// JS-P16: executeHybrid must not deep-copy an unrelated 200k-row context on every run.
import { sel, bench, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
const { Sql, Binding } = await import(pathToFileURL(root + '/js/src/sql/index.mjs').href);
const { Value } = await import(pathToFileURL(root + '/js/src/value.mjs').href);
const bindings = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM'), AMOUNT: Binding.column('amount', 'o', 'NUM') }, null, null) };
const plan = Sql.planHybrid(sel.compile('ORDERS .> FILTER(_["AMOUNT"] > 1) .> MAP(RECORD("a", _["AMOUNT"] * FACTOR)) .> TAKE(3)'), 'mariadb', bindings);
const big = Array.from({ length: 200000 }, (_, i) => ({ id: i, name: 'n' + i, v: i % 7 }));
const ctxNative = { FACTOR: 2, UNRELATED: big };
const ctxValue = Value.fromNative(ctxNative);
const runner = () => [{ AMOUNT: 5 }, { AMOUNT: 7 }, { AMOUNT: 9 }];
console.log('plan:', plan.classification ?? (plan.pureMemory ? 'pure_memory' : plan.pureSql ? 'pure_sql' : 'hybrid'));
bench('P16 executeHybrid, Value context with 200k-row table', () => String(Sql.executeHybrid(plan, runner, ctxValue).size()), { reps: 7, warm: 2 });
bench('P16 executeHybrid, small context x20000', () => { const c = Value.fromNative({ FACTOR: 2 }); let n = 0; for (let i = 0; i < 20000; i++) n += Sql.executeHybrid(plan, runner, c).size(); return n; }, { reps: 5, warm: 1 });
