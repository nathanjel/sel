#!/usr/bin/env node
// The hybrid-parity corpus (sql/oracle/hybrid.json) through the JS host, on a real
// SQLite (node:sqlite): the plan's prefix statement is EXECUTED and the answer of
// executeHybrid must be what run() answers, under the contract in that file's note.
// php/bin/sqlo runs the same corpus for the PHP host on all four servers; this is
// the JS host's own lane, and tools/check-hybrid-parity.py is the Python twin.
//
//   node tools/check-hybrid-parity.mjs [--verbose]

import { readFileSync } from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { compile, Value, SelError, registerFunction } from '../js/src/sel.mjs';
import { Sql, Binding, SqlError } from '../js/src/sql/index.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ORACLE = resolve(ROOT, 'sql/oracle');
const verbose = process.argv.includes('--verbose');
const spec = JSON.parse(readFileSync(resolve(ORACLE, 'hybrid.json'), 'utf8'));

const db = new DatabaseSync(':memory:');
const ddl = readFileSync(resolve(ORACLE, spec.fixture.sqlite), 'utf8').replace(/^\s*--.*$/gm, '');
for (const stmt of ddl.split(';')) if (stmt.trim()) db.exec(stmt);

const bindings = {};
for (const [name, b] of Object.entries(spec.bindings)) {
  const fields = {};
  for (const [f, c] of Object.entries(b.fields)) fields[f] = Binding.column(c.column, c.table, c.type);
  bindings[name] = Binding.relation(b.from, b.alias, fields, null, null);
}

const base = {};
for (const [name, rel] of Object.entries(spec.relations)) {
  base[name] = db.prepare(rel.query).all().map((r) => Object.fromEntries(rel.columns.map((c) => [c, String(r[c])])));
}

const runner = (sql, bound) => db.prepare(sql).all(...bound.map((v) => v.toNative())).map((r) =>
  Object.fromEntries(Object.entries(r).map(([k, v]) => [k, v === null ? null : String(v)])));

const norm = (x) => {
  if (Array.isArray(x)) return x.map(norm);
  if (x !== null && typeof x === 'object') return Object.fromEntries(Object.entries(x).map(([k, v]) => [k, norm(v)]));
  if (typeof x === 'boolean') return x ? 'TRUE' : 'FALSE';
  if (x === null || x === undefined) return 'NULL';
  return String(x);
};
const outcome = (fn) => {
  try {
    const v = fn();
    return ['ok', v instanceof Value ? v : Value.fromNative(v)];
  } catch (e) {
    if (e instanceof SelError) return ['err', `${e.code}@${e.line}:${e.col}`];
    if (e instanceof SqlError) return ['err', `SQL:${e.code}`];
    return ['err', `HOST:${e.constructor.name}: ${String(e.message).slice(0, 160)}`];
  }
};
const column = (native, field) => Object.values(native).map((row) =>
  row !== null && typeof row === 'object' && field in row ? norm(row[field]) : '<absent>');
const clone = (o) => JSON.parse(JSON.stringify(o));
const j = JSON.stringify;

const bad = [];
let ok = 0, skipped = 0;
const kinds = { pure_sql: 0, hybrid: 0, pure_memory: 0 };
// The corpus's `application` section (spec 8.1): programs that call the two
// application functions it describes, run once over the relations plus its
// `vars`, held to the same contract -- and so to the caller's context staying
// unwritten however POKE is reached.
registerFunction('POKE', 1, 1, (args) => { const v = args.val(0); v.set('k', Value.text('9')); return v; });
registerFunction('HOSTF', 1, 1, (args) => args.val(0));
const work = [];
for (const c of spec.programs) for (const ctxSpec of spec.contexts) work.push([c, ctxSpec]);
for (const c of spec.application?.programs ?? []) work.push([c, { name: 'application', vars: spec.application.vars }]);
for (const [c, ctxSpec] of work) {
  {
    if ((c.requires ?? []).some((v) => !(v in ctxSpec.vars))) { skipped++; continue; }
    const name = `${c.name} [${ctxSpec.name}]`;
    const fresh = () => ({ ...clone(base), ...clone(ctxSpec.vars) });
    const program = compile(c.sel);
    const direct = outcome(() => program.run(fresh()));

    const exp = c.expect ?? {};
    let guard = null;
    if (exp.error) {
      if (direct[0] !== 'err' || !direct[1].startsWith(`${exp.error}@`)) guard = `SEL answered ${j(direct[0] === 'err' ? direct[1] : 'a value')} where the corpus says ${exp.error}`;
    } else if (direct[0] !== 'ok') {
      guard = `SEL raised ${direct[1]}`;
    } else {
      for (const [f, want] of Object.entries(exp.fields ?? {})) {
        const got = column(direct[1].toNative(), f);
        if (j(got) !== j(want)) guard = `run() column ${f} is ${j(got)}, the corpus says ${j(want)}`;
      }
      if ('value' in exp && j(norm(direct[1].toNative())) !== j(exp.value)) guard = `run() is ${j(norm(direct[1].toNative()))}, the corpus says ${j(exp.value)}`;
      if (exp.keys && j(direct[1].keys().map(String)) !== j(exp.keys)) guard = `run() keys are ${j(direct[1].keys())}, the corpus says ${j(exp.keys)}`;
    }
    if (guard) { bad.push([name, '', `CORPUS: ${guard}`]); continue; }

    let plan;
    try { plan = Sql.planHybrid(program, 'sqlite', bindings); } catch (e) { bad.push([name, '', `planHybrid raised ${e.constructor.name}: ${e.message}`]); continue; }
    const kind = plan.kind;
    kinds[kind]++;
    const statement = plan.sqlStatement ? plan.sqlStatement.asStatement() : '-';

    const caller = Value.fromNative(fresh());
    const before = caller.dump();
    const exec = outcome(() => Sql.executeHybrid(plan, runner, caller));
    const after = caller.dump();

    let why = null;
    if (direct[0] === 'err') {
      if (exec[0] !== 'err' || exec[1] !== direct[1]) why = `run() raises ${direct[1]} and the executed ${kind} plan gives ${exec[0] === 'err' ? exec[1] : 'a value'}`;
    } else if (exec[0] === 'err') {
      why = `the executed ${kind} plan raises ${exec[1]} where run() answers`;
    } else {
      let want = norm(direct[1].toNative()), got = norm(exec[1].toNative());
      if (kind === 'pure_sql') { want = Object.values(want); got = Object.values(got); } else {
        want = { keys: direct[1].keys().map(String), rows: Object.values(want) };
        got = { keys: exec[1].keys().map(String), rows: Object.values(got) };
      }
      if (j(want) !== j(got)) why = `run()=${j(want)}\n        plan=${j(got)}`;
    }
    if (!why && before !== after) why = `executeHybrid changed the caller's context (${kind} plan)`;
    if (why) { bad.push([name, `[${kind}] ${statement}`, why]); continue; }
    ok++;
    if (verbose) console.log(`  ok       ${name.padEnd(64)} ${kind}`);
  }
}
for (const c of spec.bounded ?? []) {
  const program = compile(c.sel);
  const t0 = performance.now();
  try { Sql.planHybrid(program, 'sqlite', bindings); } catch { /* a refusal is an answer */ }
  const ms = performance.now() - t0;
  if (ms > c.max_ms) bad.push([c.name, '', `planning took ${ms.toFixed(0)} ms, the bound is ${c.max_ms}`]); else ok++;
}
for (const [name, statement, why] of bad) console.log(`DIFFER  ${name}\n        ${statement}\n        ${why}`);
console.log(`js hybrid parity (sqlite): ${ok} agree, ${bad.length} differ (plans: ${kinds.pure_sql} pure_sql, ${kinds.hybrid} hybrid, ${kinds.pure_memory} pure_memory; ${skipped} variants skipped)`);
process.exit(bad.length === 0 ? 0 : 1);
