#!/usr/bin/env node
// SQL API parity probe: the planner's contract through every host's own SQL
// binding. tools/check-sqlapi.sh diffs the reports of the hosts that carry the
// SQL layer (the JS bundles do not; they print nothing and are left out).
// Three programs -- one the planner pushes down whole, one it splits into a SQL
// prefix and an in-memory continuation, one it keeps in memory -- and the same
// nine questions about each plan: its classification, dialect, statement, prefix
// and continuation presence, the continuation's dependencies, its source
// variable, the physical source tables and the selected member. The probe NAMES
// are the contract and the VALUES are compared; each host spells its accessors
// its own way (SEL-0044).
import { compile, evaluate, registerFunction, Value } from '../js/src/sel.mjs';
import { Sql, Binding, Fragment, SqlError, map as sqlmap } from '../js/src/sql/index.mjs';

const out = [];
let n = 0;
const say = (name, value) => out.push(`${String(++n).padStart(2, '0')} ${name} = ${value}`);
const bool = (b) => (b ? 'true' : 'false');
const bindings = {
  ORDERS: Binding.relation('orders', 'o', {
    ID: Binding.column('id', 'o', 'NUM'), CUSTOMER_ID: Binding.column('customer_id', 'o', 'NUM'),
    AMOUNT: Binding.column('amount', 'o', 'NUM'), NAME: Binding.column('name', 'o', 'TEXT') }, null, null),
  CUSTOMERS: Binding.relation('customers', 'c', {
    ID: Binding.column('id', 'c', 'NUM'), NAME: Binding.column('name', 'c', 'TEXT') }, null, null),
};
const probe = (label, source) => {
  const plan = Sql.planHybrid(compile(source), 'mariadb', bindings);
  say(`plan.${label}.kind`, plan.pureSql ? 'pure_sql' : plan.pureMemory ? 'pure_memory' : 'hybrid');
  say(`plan.${label}.dialect`, plan.dialect ?? '-');
  say(`plan.${label}.statement`, plan.sqlStatement ? plan.sqlStatement.asStatement() : '-');
  say(`plan.${label}.prefix.present`, bool(plan.sqlPrefixAst !== null && plan.sqlPrefixAst !== undefined));
  say(`plan.${label}.continuation.present`, bool(plan.continuationAst !== null && plan.continuationAst !== undefined));
  say(`plan.${label}.continuation.deps`, plan.continuationProgram ? plan.continuationProgram.dependencies().join(' ') : '-');
  say(`plan.${label}.source.var`, plan.continuationSourceVar);
  say(`plan.${label}.tables`, (plan.sourceTables ?? []).join(','));
  say(`plan.${label}.selected.member`, plan.selectedMember ? 'present' : '-');
};
probe('sql', 'ORDERS .> FILTER(_["AMOUNT"] > 10) .> MAP(RECORD("id", _["ID"], "amount", _["AMOUNT"]))');
probe('hybrid', 'ORDERS .> SORT_BY(_["AMOUNT"]) .> FILTER(_K > 1)');
probe('memory', 'A += 1; ORDERS .> TAKE(1)');
// The canonical flag is public: an application (and the SQL oracle) reads it
// to know the fragment promised a spelling, not only a value (SEL-0058).
const fragmentProbe = (label, dialect, source) => {
  const f = Sql.translate(compile(source), dialect, bindings);
  say(`fragment.${label}.kind`, f.kind);
  say(`fragment.${label}.canonical`, bool(f.canonical));
  say(`fragment.${label}.caveats`, f.caveats.join(',') || '-');
};
fragmentProbe('canon.postgresql', 'postgresql', 'CANON(1.50)');
fragmentProbe('canon.mariadb', 'mariadb', 'CANON(1.50)');
fragmentProbe('canon.sqlite', 'sqlite', 'CANON(1.50)');
fragmentProbe('abs.postgresql', 'postgresql', 'ABS(1.50)');

// --- host functions with a SQL spelling (spec §8.1, sql/MAP.md §4.7) ----------
// The registration order, the caveat, strict mode, a LIST argument, a builder,
// reset() and a re-registration, through this host's own spelling of the API.
const HOST = { T: Binding.column('title', 't', 'TEXT') };
const attempt = (fn) => {
  try {
    fn();
    return 'accepted';
  } catch (e) {
    if (e instanceof SqlError) return `SqlError ${e.code}`;
    if (e instanceof Error) return 'refused';
    throw e;
  }
};

say('host.spell.before-register', attempt(() => sqlmap.define(
  'postgresql', 'funcs', 'HSLUG', { tpl: 'slug({0})', ret: 'TEXT' })));
registerFunction('HSLUG', 1, 1, (a) => Value.text('local:' + a.text(0)));
sqlmap.define('postgresql', 'funcs', 'HSLUG', { tpl: 'slug({0})', ret: 'TEXT', args: ['TEXT'] });
const spelled = Sql.translate(compile('HSLUG(T) $== "x"'), 'postgresql', HOST);
say('host.spell.condition', spelled.asCondition());
say('host.spell.caveats', spelled.caveats.join(',') || '-');
say('host.spell.strict', attempt(() => Sql.translate(
  compile('HSLUG(T)'), 'postgresql', HOST, { strict: true })));
say('host.spell.other-dialect', attempt(() => Sql.translate(compile('HSLUG(T)'), 'mariadb', HOST)));

registerFunction('HHAS', 2, 2, () => Value.bool(false));
sqlmap.define('postgresql', 'funcs', 'HHAS',
  { tpl: '({1} = ANY(ARRAY[{0}]))', ret: 'BOOL', args: ['LIST', 'TEXT'] });
const listed = Sql.translate(compile('HHAS(("a", "b"), "c")'), 'postgresql', HOST);
say('host.spell.list.params', listed.asCondition('params'));
say('host.spell.list.bound', listed.bindings().map((v) => v.dump()).join(','));

registerFunction('HWRAP', 1, 1, (a) => a.val(0).clone());
sqlmap.defineBuilder('postgresql', 'funcs', 'HWRAP',
  (emit, args) => new Fragment(['wrap(', ...args[0].parts, ')'], 'TEXT', emit.dialect()));
say('host.spell.builder', Sql.translate(compile('HWRAP(T)'), 'postgresql', HOST).asValue());

registerFunction('HSLUG', 1, 2, (a) => Value.text('local:' + a.text(0)));
say('host.spell.reregistered-arity', attempt(() => Sql.translate(compile('HSLUG(T)'), 'postgresql', HOST)));
registerFunction('HSLUG', 1, 1, (a) => Value.text('local:' + a.text(0)));
say('host.spell.arity-restored', attempt(() => Sql.translate(compile('HSLUG(T)'), 'postgresql', HOST)));
sqlmap.reset();
say('host.spell.after-reset', attempt(() => Sql.translate(compile('HSLUG(T)'), 'postgresql', HOST)));
say('host.spell.after-reset.local', evaluate('HSLUG("A")').asText());

process.stdout.write(out.join('\n') + '\n');
