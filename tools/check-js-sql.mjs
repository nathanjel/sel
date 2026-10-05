#!/usr/bin/env node
// JS SQL layer: regression checks that are about the host, not the shared corpus
// (sql/cases holds the portable ones). T08-T11 of the 2026-09-29 review:
// bounded work on hostile programs, no host exception where an answer is owed,
// and hybrid execution that never writes the caller's context.
//
//     node tools/check-js-sql.mjs

import { compile, Value } from '../js/src/sel.mjs';
import * as sql from '../js/src/sql/index.mjs';
import { registerFunction, define } from '../js/src/registry.mjs';

const { Binding, Sql, SqlError } = sql;
let failures = 0;
let count = 0;
const check = (name, ok, detail = '') => {
  count += 1;
  if (!ok) { failures += 1; console.error(`FAIL ${name} ${detail}`); }
};
const outcome = (f) => {
  try { return { value: f() }; } catch (e) { return { error: e }; }
};

const N = { N: Binding.column('n', 't', 'NUM') };
const chain = (n) => {
  let src = 'X0 = N; ';
  for (let i = 1; i <= n; i++) src += `X${i} = X${i - 1} + 1; `;
  return `${src}X${n} > 0`;
};

// --- a chain of helpers as deep as the source is long -----------------------
for (const n of [3000, 20000, 100000]) {
  const t0 = Date.now();
  const r = outcome(() => Sql.translate(compile(chain(n)), 'mariadb', N));
  check(`chain ${n}: translate answers E_SQL_DEPTH, not a host RangeError`,
    r.error instanceof SqlError && r.error.code === 'E_SQL_DEPTH', String(r.error));
  check(`chain ${n}: tryTranslate returns null`,
    outcome(() => Sql.tryTranslate(compile(chain(n)), 'mariadb', N)).value === null);
  const plan = outcome(() => sql.planHybrid(compile(chain(n)), 'mariadb', N));
  check(`chain ${n}: planHybrid answers a pure-memory plan`,
    plan.value && plan.value.pureMemory === true, String(plan.error));
  check(`chain ${n}: bounded work (${Date.now() - t0} ms)`, Date.now() - t0 < 15000);
}

const R = { ORDERS: Binding.relation('orders', 'o', { ID: Binding.column('id', 'o', 'NUM') }) };
{
  const flat = compile(`ORDERS${' .> TAKE(1)'.repeat(20000)}`);
  const plan = outcome(() => sql.planHybrid(flat, 'mariadb', R));
  check('a flat 20000-step pipeline plans as pure memory, no RangeError',
    plan.value && plan.value.pureMemory === true && plan.value.sourceTables.join() === 'orders',
    String(plan.error));
}

// --- expansion budget --------------------------------------------------------
{
  let src = 'X0 = N; ';
  for (let i = 1; i <= 40; i++) src += `X${i} = X${i - 1} + X${i - 1}; `;
  const t0 = Date.now();
  const r = outcome(() => Sql.translate(compile(`${src}X40 > 0`), 'mariadb', N));
  check('a 2^40 doubling chain is refused E_SQL_SIZE quickly',
    r.error instanceof SqlError && r.error.code === 'E_SQL_SIZE' && Date.now() - t0 < 5000,
    String(r.error));
}

// --- a NULL element of a value binding ---------------------------------------
{
  const V = { V: Binding.value(Value.list([Value.num('1'), Value.null()])) };
  for (const src of ['COUNT(V)', 'ANY(V, _ == 1)', 'JOIN(V, ",")', 'V[2] == 1']) {
    const r = outcome(() => Sql.tryTranslate(compile(src), 'mariadb', V));
    check(`tryTranslate(${src}) with a NULL element never throws a host error`,
      r.error === undefined, String(r.error));
    check(`tryTranslate(${src}) with a NULL element is a refusal, not a fragment`,
      r.value === null, String(r.value));
  }
}

// --- text and names from the program ------------------------------------------
{
  const r = outcome(() => Sql.translate(compile('S $== "a\u{0}b"'.replace('\u{0}', '\\u{0}')), 'mariadb',
    { S: Binding.column('s', 'o', 'TEXT') }));
  check('a NUL in a text literal is refused in inline mode',
    r.error instanceof SqlError && r.error.code === 'E_SQL_UNSUPPORTED', String(r.error));
  const dup = outcome(() => new sql.Bindings({ x: Binding.column('a'), X: Binding.column('b') }));
  check('two bindings differing only by case are refused',
    dup.error instanceof SqlError && dup.error.code === 'E_SQL_BINDING');
  const fields = outcome(() => Binding.relation('t', 't', { A: Binding.column('x'), a: Binding.column('y') }));
  check('two fields differing only by case are refused',
    fields.error instanceof SqlError && fields.error.code === 'E_SQL_BINDING');
  for (const type of ['LIST', 'STATEMENT']) {
    const b = outcome(() => Binding.column('c', 'o', type));
    check(`a column declared ${type} is refused`,
      b.error instanceof SqlError && b.error.code === 'E_SQL_BINDING');
  }
}

// --- dialect registration -----------------------------------------------------
{
  const { map } = sql;
  const bad = [
    ['my-nobs', { extends: 'mariadb', version: '10.5', lexical: { textEscape: { '\\': '\\\\' } } }],
    ['sqlite-noesc', { extends: 'sqlite', version: '3.48', lexical: { textEscape: {} } }],
    ['my-badbs', { extends: 'mariadb', version: '10.5', lexical: { textEscape: { "'": "\\'" } } }],
    ['dq', { extends: 'ansi', version: '1', lexical: { textQuote: '"' } }],
  ];
  for (const [name, spec] of bad) {
    check(`registering ${name} with an unsafe quote/escape pairing throws`,
      outcome(() => map.defineDialect(name, spec)).error instanceof Error);
    check(`${name} was not registered`, !map.exists(name));
  }
  map.defineDialect('js-redef', { extends: 'mariadb', version: '10.5' });
  check('the same parent may re-declare a registered dialect',
    outcome(() => map.defineDialect('js-redef', { extends: 'mariadb', version: '10.6' })).error === undefined);
  check('a different parent may not',
    outcome(() => map.defineDialect('js-redef', { extends: 'postgresql', version: '16' })).error instanceof Error);
  check('a shipped dialect may not be re-declared',
    outcome(() => map.defineDialect('mariadb', { extends: null, version: '11' })).error instanceof Error);
}

// --- text literals: the regex pass and the per-character scan agree (JS-P25) ------
{
  const { map } = sql;
  const { textLiteral } = await import('../js/src/sql/emit.mjs');
  map.defineDialect('js-esc', { extends: 'mariadb', version: '10.5',
    lexical: { textEscape: { "'": "''", '\\': '\\\\', '--': '-\\-', '\n': '\\n', '\\\\': 'BACKSLASH-PAIR' } } });
  const naive = (dialect, text) => {
    const esc = map.lexical(dialect, 'textEscape');
    const keys = Object.keys(esc).filter((k) => k).sort((a, b) => b.length - a.length);
    let out = '';
    for (let i = 0; i < text.length;) {
      const hit = keys.find((k) => text.startsWith(k, i));
      if (hit === undefined) { out += text[i]; i += 1; } else { out += String(esc[hit]); i += hit.length; }
    }
    return String(map.lexical(dialect, 'textQuote')) + out + String(map.lexical(dialect, 'textQuote'));
  };
  const parts = ["'", '\\', '\\\\', '--', '-', '\n', 'a', ' ', '😀', 'é'];
  let seed = 7;
  const next = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  let agree = true;
  for (let i = 0; i < 400 && agree; i++) {
    let text = '';
    const len = 20 + Math.floor(next() * 300);
    for (let k = 0; k < len; k++) text += parts[Math.floor(next() * parts.length)];
    for (const d of ['mariadb', 'postgresql', 'sqlite', 'js-esc']) if (textLiteral(d, text) !== naive(d, text)) agree = false;
  }
  check('textLiteral over long text equals the per-character rule in every dialect, overlapping keys included', agree);
  // The rules object is the host's own and nothing freezes it: a rule added after the
  // first long literal is honoured by the next one.
  const rules = map.lexical('js-esc', 'textEscape');
  const long = 'x%y'.repeat(40);
  const before = textLiteral('js-esc', long);
  rules['%'] = '\\%';
  const after = textLiteral('js-esc', long);
  delete rules['%'];
  check('a rule added to a dialect\'s escape table after use applies to the next literal',
    before === naive('js-esc', long) && after.includes('x\\%y') && textLiteral('js-esc', long) === before);
}

// --- the dialect chain is cached, frozen, and follows registrations (JS-P26) -------
{
  const { map } = sql;
  check('chain(mariadb) is self first, then up to ansi', JSON.stringify(map.chain('mariadb')) === '["mariadb","mysql-family","ansi"]',
    JSON.stringify(map.chain('mariadb')));
  check('the same array comes back (built once) and it is frozen',
    map.chain('mariadb') === map.chain('mariadb') && Object.isFrozen(map.chain('mariadb')));
  check('an unknown name has an empty chain for now', map.chain('js-later').length === 0);
  map.defineDialect('js-later', { extends: 'postgresql', version: '16' });
  check('...and the chain follows once it is registered',
    JSON.stringify(map.chain('js-later')) === '["js-later","postgresql","ansi"]', JSON.stringify(map.chain('js-later')));
  check('lexical() and entry() walk the new chain', map.lexical('js-later', 'textQuote') === "'");
  map.defineDialect('js-later', { extends: 'postgresql', version: '17' });
  check('re-declaring under the same parent keeps a correct chain',
    JSON.stringify(map.chain('js-later')) === '["js-later","postgresql","ansi"]');
}

// --- hybrid execution never writes the caller's context ----------------------------
{
  const program = compile('A = 1; A += 1; ORDERS .> SORT_BY(_["id"]) .> TAKE(A)');
  const plan = sql.planHybrid(program, 'mariadb', R);
  const context = Value.fromNative({});
  const run = outcome(() => sql.executeHybrid(plan, () => [], context));
  check('executeHybrid on a pure-memory plan leaves the caller\'s context alone',
    context.size() === 0, `${context.size()} keys`);
  // (No ORDERS in the context: the program's own E_UNDEF_VAR is the honest answer.)
  check('...and answers as run() does', run.error === undefined || run.error.code === 'E_UNDEF_VAR',
    String(run.error));
}

// 1. Pure-memory plan invokes mutating callback
{
  registerFunction('JS_POKE_1', 1, 1, (args) => {
    const v = args.val(0);
    v.set('k', Value.text('9'));
    return v;
  });
  const ctx = Value.fromNative({ A: { k: '1' } });
  const plan = sql.planHybrid(compile('JS_POKE_1(A)'), 'sqlite');
  check('pure-memory plan invokes mutating callback: pureMemory', plan.pureMemory === true);
  const res = sql.executeHybrid(plan, null, ctx);
  check('pure-memory plan invokes mutating callback: result reflects mutation', res.get('k').asText() === '9');
  check('pure-memory plan invokes mutating callback: caller context unchanged', ctx.get('A').get('k').asText() === '1');
}

// 2. Callback mutates then raises error
{
  registerFunction('JS_POKE_ERR', 1, 1, (args) => {
    const v = args.val(0);
    v.set('k', Value.text('99'));
    throw new sql.SqlError('E_CUSTOM', 'callback error');
  });
  const ctx = Value.fromNative({ A: { k: '1' } });
  const plan = sql.planHybrid(compile('JS_POKE_ERR(A)'), 'sqlite');
  const run = outcome(() => sql.executeHybrid(plan, null, ctx));
  check('callback mutates then raises: error propagates', run.error !== undefined);
  check('callback mutates then raises: caller context unchanged', ctx.get('A').get('k').asText() === '1');
}

// 3. Callback nested inside aggregate or lazy branch
{
  registerFunction('JS_POKE_NESTED', 1, 1, (args) => {
    const v = args.val(0);
    v.set('k', Value.text('999'));
    return v;
  });
  const ctx1 = Value.fromNative({ A: { k: '1' } });
  const plan1 = sql.planHybrid(compile('IF(TRUE, JS_POKE_NESTED(A), 0)'), 'sqlite');
  sql.executeHybrid(plan1, null, ctx1);
  check('callback in lazy branch: caller context unchanged', ctx1.get('A').get('k').asText() === '1');

  const ctx2 = Value.fromNative({ A: { k: '1' } });
  const plan2 = sql.planHybrid(compile('MAP(LIST(1), JS_POKE_NESTED(A))'), 'sqlite');
  sql.executeHybrid(plan2, null, ctx2);
  check('callback in aggregate: caller context unchanged', ctx2.get('A').get('k').asText() === '1');
}

// 4. Application function uses lower-level definition API
{
  define({
    name: 'JS_POKE_LOW',
    min: 1,
    max: 1,
    fn: (args) => {
      const v = args.val(0);
      v.set('k', Value.text('888'));
      return v;
    },
  });
  const ctx = Value.fromNative({ A: { k: '1' } });
  const plan = sql.planHybrid(compile('JS_POKE_LOW(A)'), 'sqlite');
  sql.executeHybrid(plan, null, ctx);
  check('define API callback: caller context unchanged', ctx.get('A').get('k').asText() === '1');
}

// 5. SQL prefix followed by a local callback
{
  registerFunction('JS_POKE_SPLIT', 1, 1, (args) => {
    const v = args.val(0);
    v.set('k', Value.text('777'));
    return v;
  });
  const src = 'ORDERS .> SORT_BY(_["id"]) .> MAP(JS_POKE_SPLIT(A))';
  const plan = sql.planHybrid(compile(src), 'sqlite', R);
  check('split plan with callback: not pure memory', plan.pureMemory === false);
  check('split plan with callback: has sqlStatement', plan.sqlStatement !== null);
  let calls = 0;
  const runner = () => {
    calls++;
    return Value.fromNative([{ id: 1 }, { id: 2 }]);
  };
  const ctx = Value.fromNative({ A: { k: '1' } });
  const res = sql.executeHybrid(plan, runner, ctx);
  check('split plan runner called once', calls === 1);
  check('split plan result reflects mutation', res.values()[0].get('k').asText() === '777');
  check('split plan caller context unchanged', ctx.get('A').get('k').asText() === '1');
}

// 6. Same plan executes twice
{
  registerFunction('JS_POKE_TWICE', 1, 1, (args) => {
    const v = args.val(0);
    v.set('k', Value.text('555'));
    return v;
  });
  const plan = sql.planHybrid(compile('JS_POKE_TWICE(A)'), 'sqlite');
  const ctx1 = Value.fromNative({ A: { k: '1' } });
  const ctx2 = Value.fromNative({ A: { k: '1' } });
  sql.executeHybrid(plan, null, ctx1);
  sql.executeHybrid(plan, null, ctx2);
  check('same plan twice: ctx1 unchanged', ctx1.get('A').get('k').asText() === '1');
  check('same plan twice: ctx2 unchanged', ctx2.get('A').get('k').asText() === '1');
}

// 7. Builtin-only, read-only continuation with a large context
{
  const inner = Value.fromNative({ k: '1' });
  const ctx = Value.fromNative({ A: inner });
  const plan = sql.planHybrid(compile('A["k"]'), 'sqlite');
  const res = sql.executeHybrid(plan, null, ctx);
  check('read-only continuation answers correctly', res.asText() === '1');
}

// 8. Builtin-only continuation assigns to nested field
{
  const ctx = Value.fromNative({ A: { k: '1' } });
  const plan = sql.planHybrid(compile('A["k"] = "99"; A'), 'sqlite');
  const res = sql.executeHybrid(plan, null, ctx);
  check('builtin assignment: result reflects assign', res.get('k').asText() === '99');
  check('builtin assignment: caller context unchanged', ctx.get('A').get('k').asText() === '1');
}

// 9. AST replacement from read-only to callback invalidates cache
{
  registerFunction('JS_POKE_REPLACE', 1, 1, (args) => {
    const v = args.val(0);
    v.set('k', Value.text('333'));
    return v;
  });
  const prog = compile('A');
  const plan = sql.planHybrid(prog, 'sqlite');
  const ctx = Value.fromNative({ A: { k: '1' } });
  const r1 = sql.executeHybrid(plan, null, ctx);
  check('ast replace: r1 correct', r1.get('k').asText() === '1');
  check('ast replace: ctx unchanged initially', ctx.get('A').get('k').asText() === '1');

  prog.ast = compile('JS_POKE_REPLACE(A)').ast;
  const r2 = sql.executeHybrid(plan, null, ctx);
  check('ast replace: r2 reflects callback', r2.get('k').asText() === '333');
  check('ast replace: ctx unchanged after replacement', ctx.get('A').get('k').asText() === '1');
}

// A Bindings instance is accepted wherever a plain map of bindings is: by
// translate and translateStatement as by planHybrid (and by Python's twins).
{
  const catalog = new sql.Bindings({ X: Binding.column('x', null, 'NUM'), ...R });
  const viaMap = { X: Binding.column('x', null, 'NUM'), ...R };
  const t = outcome(() => Sql.translate(compile('X > 1'), 'sqlite', catalog).asCondition());
  check('translate takes a Bindings instance', t.value === Sql.translate(compile('X > 1'), 'sqlite', viaMap).asCondition(),
    t.error ? `${t.error.code} ${t.error.message}` : t.value);
  const pipe = compile('ORDERS .> FILTER(_["ID"] > 1)');
  const s = outcome(() => Sql.translateStatement(pipe, 'sqlite', catalog).asStatement());
  check('translateStatement takes a Bindings instance', s.value === Sql.translateStatement(pipe, 'sqlite', viaMap).asStatement(),
    s.error ? `${s.error.code} ${s.error.message}` : s.value);
  const copy = outcome(() => new sql.Bindings(catalog).names().join());
  check('a Bindings built from a Bindings keeps its names', copy.value === catalog.names().join(), copy.error?.message);
}

// A source the program reassigns and a continuation step then reads as a value
// (sql/cases/48-scope-and-slots.sqlt pins only the form no step reads): the SQL
// takes the DROP as its OFFSET, and COUNT(ORDERS) in the continuation is the
// reassigned helper (4 rows), not the whole relation. The prefix runs on a real
// SQLite, and run() over the same rows is the answer.
{
  const { DatabaseSync } = await import('node:sqlite');
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE orders (id INTEGER); INSERT INTO orders VALUES (1),(2),(3),(4),(5),(6);');
  registerFunction('JS_REREAD_HOSTF', 1, 1, (args) => args.val(0));
  const src = 'ORDERS = ORDERS .> DROP(2); '
    + 'ORDERS .> TAKE(3) .> MAP(RECORD("n", COUNT(ORDERS), "x", JS_REREAD_HOSTF(_["ID"])))';
  const plan = sql.planHybrid(compile(src), 'sqlite', R);
  const stmt = plan.sqlStatement ? plan.sqlStatement.asStatement('inline') : '';
  check('reread source: the SQL prefix is LIMIT 3 OFFSET 2', /LIMIT 3 OFFSET 2$/.test(stmt), stmt);
  const runner = (q, bound) => db.prepare(q).all(...bound.map((v) => v.toNative()))
    .map((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k.toUpperCase(), String(v)])));
  const rows = () => Value.fromNative({ ORDERS: db.prepare('SELECT id FROM orders').all().map((r) => ({ ID: String(r.id) })) });
  const ctx = rows();
  const before = ctx.dump();
  const hybrid = outcome(() => sql.executeHybrid(plan, runner, ctx));
  const direct = outcome(() => compile(src).run(rows()));
  check('reread source: hybrid equals run()', hybrid.value !== undefined && direct.value !== undefined
    && hybrid.value.dump() === direct.value.dump(),
    `${hybrid.value?.dump() ?? hybrid.error} vs ${direct.value?.dump() ?? direct.error}`);
  check('reread source: n is 4 in three rows', direct.value?.dump()
    === '-{"1"=-{"n"=t"4", "x"=t"3"}, "2"=-{"n"=t"4", "x"=t"4"}, "3"=-{"n"=t"4", "x"=t"5"}}', direct.value?.dump());
  check('reread source: the caller\'s context is not written', ctx.dump() === before);
}

console.log(count === 0 ? 'no checks' : `js sql: ${count - failures}/${count} checks pass`);
process.exit(failures === 0 ? 0 : 1);
