#!/usr/bin/env node
// JS SQL layer: regression checks that are about the host, not the shared corpus
// (sql/cases holds the portable ones). T08-T11 of the 2026-09-29 review:
// bounded work on hostile programs, no host exception where an answer is owed,
// and hybrid execution that never writes the caller's context.
//
//     node tools/check-js-sql.mjs

import { compile, Value } from '../js/src/sel.mjs';
import * as sql from '../js/src/sql/index.mjs';

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

console.log(count === 0 ? 'no checks' : `js sql: ${count - failures}/${count} checks pass`);
process.exit(failures === 0 ? 0 : 1);
