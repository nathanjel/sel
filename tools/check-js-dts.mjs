#!/usr/bin/env node
// The shipped TypeScript declarations against the modules they describe.
//
// js/src/sel.d.ts and js/src/sql.d.ts are hand-written, and nothing compared
// them with the code: sql.d.ts drifted to a `map` namespace with functions that
// do not exist, a KINDS export that was never exported, plan fields that were
// renamed, and Binding constructors without half their parameters. No
// TypeScript compiler is a dependency of this repository, so this reads the
// declarations itself -- they are written in a plain, regular style, one member
// per `;` -- and compares, both ways:
//
//   * every declared runtime export, class member, namespace member and
//     method parameter list exists, with the same parameter names in order;
//   * every runtime export and every public member is declared, unless it is
//     listed in INTERNAL below with the reason it stays out of the typings.
//
// Types are not checked; names, members and parameter lists are.
//
//     node tools/check-js-dts.mjs

import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sel = await import(join(ROOT, 'js/src/sel.mjs'));
const sql = await import(join(ROOT, 'js/src/sql/index.mjs'));

// Public at run time (JS has no package-private), used across js/src, and
// deliberately not part of the declared interface.
const INTERNAL = {
  Value: {
    // The evaluator's trusted constructors: no checks, no copies.
    static: ['textOwned', 'binOwned', 'shapedOwned', 'shapedFromShape', 'fromEntriesOwned',
      'fromEntriesPreserveDuplicates', 'byteValue', 'numOwned', 'listOwned'],
    // Storage and evaluator helpers.
    instance: ['children', 'shape', 'storage', 'asTextOrBytes', 'tryDecimal', 'shallowRoot',
      'checkDepthAt'],
  },
  // The translator's own composition state; `exact` (the collation meaning) is declared.
  Fragment: { instance: ['sargable', 'guard', 'prefilter', 'separatePrefilter', 'sumTest'] },
  // Helpers the translator calls through the same module.
  map: { members: ['checkNumericGuard', 'hostSpellingArity', 'requireTarget', 'versionAtLeast'] },
};

// Built-in members no declaration repeats.
const ERROR_MEMBERS = new Set(['stack', 'message', 'name', 'cause']);

let failures = 0;
let checked = 0;
const fail = (what) => { failures += 1; console.log(`FAIL ${what}`); };

// --- reading the declarations ---------------------------------------------

function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
}

// The text between the brace at `open` and its partner.
function block(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === '{') depth++;
    else if (text[i] === '}' && --depth === 0) return { body: text.slice(open + 1, i), end: i + 1 };
  }
  throw new Error('unbalanced braces');
}

// Statements separated by `;` outside any bracket.
function statements(body) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of body) {
    if ('({[<'.includes(ch)) depth++;
    else if (')}]>'.includes(ch) && !(ch === '>' && cur.endsWith('='))) depth--;
    if (ch === ';' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out.filter(Boolean);
}

// Parameter names of a `(...)` list starting at `text[open]`.
function paramNames(text, open) {
  let depth = 0;
  let cur = '';
  const parts = [];
  for (let i = open; i < text.length; i++) {
    const ch = text[i];
    if ('({[<'.includes(ch)) { depth++; if (depth === 1) continue; }
    if (')}]>'.includes(ch) && !(ch === '>' && text[i - 1] === '=')) {
      depth--;
      if (depth === 0) { parts.push(cur); break; }
    }
    if (ch === ',' && depth === 1) { parts.push(cur); cur = ''; continue; }
    cur += ch;
  }
  return parts.map((p) => p.trim()).filter(Boolean)
    .map((p) => /^(?:\.\.\.)?\s*([A-Za-z_$][\w$]*)/.exec(p)?.[1] ?? p);
}

const MEMBER = /^(?:(static)\s+)?(?:(readonly)\s+)?(?:(get|set)\s+)?([A-Za-z_$][\w$]*)\s*\??\s*([(:<])/;

function readClass(body) {
  const members = { static: new Map(), instance: new Map(), ctor: undefined };
  for (const st of statements(body)) {
    const m = MEMBER.exec(st);
    if (!m) throw new Error(`cannot read the member declaration: ${st.slice(0, 60)}`);
    const [, isStatic, , accessor, name, after] = m;
    const params = after === '(' && !accessor ? paramNames(st, st.indexOf('(', m[0].length - 1)) : null;
    if (name === 'constructor') { members.ctor = params; continue; }
    const table = isStatic ? members.static : members.instance;
    if (!table.has(name)) table.set(name, []);
    table.get(name).push(params);
  }
  return members;
}

function readDeclarations(file) {
  const text = stripComments(readFileSync(join(ROOT, file), 'utf8'));
  const decl = { values: new Map(), classes: new Map(), namespaces: new Map() };
  const re = /export\s+(?:declare\s+)?(class|function|const|namespace|interface|type)\s+([A-Za-z_$][\w$]*)/g;
  let m;
  while ((m = re.exec(text))) {
    const [, kind, name] = m;
    if (kind === 'interface' || kind === 'type') continue;
    if (kind === 'class') {
      const open = text.indexOf('{', re.lastIndex);
      const { body, end } = block(text, open);
      decl.classes.set(name, readClass(body));
      decl.values.set(name, 'class');
      re.lastIndex = end;
    } else if (kind === 'namespace') {
      const open = text.indexOf('{', re.lastIndex);
      const { body, end } = block(text, open);
      const members = new Map();
      for (const st of statements(body)) {
        const n = /export\s+(?:declare\s+)?(function|const)\s+([A-Za-z_$][\w$]*)/.exec(st);
        if (!n) throw new Error(`cannot read the namespace member: ${st.slice(0, 60)}`);
        if (!members.has(n[2])) members.set(n[2], []);
        members.get(n[2]).push(n[1] === 'function' ? paramNames(st, st.indexOf('(')) : null);
      }
      decl.namespaces.set(name, members);
      decl.values.set(name, 'namespace');
      re.lastIndex = end;
    } else if (kind === 'function') {
      if (!decl.values.has(name)) decl.values.set(name, []);
      decl.values.get(name).push(paramNames(text, text.indexOf('(', re.lastIndex)));
    } else {
      decl.values.set(name, 'const');
    }
  }
  return decl;
}

// --- reading the code -----------------------------------------------------

// Parameter names from a function's own source text.
// A class's are its constructor's, none when it has no constructor of its own.
// A trailing `_` is how a parameter avoids shadowing a module function
// (map.define's `entry_`), not part of its name.
function runtimeParams(fn) {
  const src = Function.prototype.toString.call(fn);
  let open = src.indexOf('(');
  if (/^class\b/.test(src)) {
    const m = /^[ \t]*constructor\s*\(/m.exec(src);
    if (!m) return [];
    open = m.index + m[0].length - 1;
  }
  return paramNames(src, open).map((p) => p.replace(/_$/, ''));
}

function compareParams(where, declared, fn) {
  checked++;
  if (typeof fn !== 'function') { fail(`${where} is declared as a function and is a ${typeof fn}`); return; }
  // An overloaded declaration describes call shapes, not the one parameter list.
  if (declared.length !== 1 || declared[0] === null) return;
  const got = runtimeParams(fn);
  const want = declared[0];
  if (got.join(',') !== want.join(',')) {
    fail(`${where}: declared (${want.join(', ')}), the code takes (${got.join(', ')})`);
  }
}

function prototypeNames(cls) {
  const out = new Set();
  for (let p = cls.prototype; p && p !== Object.prototype && p !== Error.prototype; p = Object.getPrototypeOf(p)) {
    for (const k of Object.getOwnPropertyNames(p)) if (k !== 'constructor') out.add(k);
  }
  return out;
}

function compareClass(where, cls, declared, sample, internal = {}) {
  const hidden = (k) => k.startsWith('_') || ERROR_MEMBERS.has(k);
  // Static members.
  const staticNames = new Set(Object.getOwnPropertyNames(cls).filter((k) => !['length', 'name', 'prototype'].includes(k)));
  for (const [name, overloads] of declared.static) {
    checked++;
    if (!staticNames.has(name)) { fail(`${where}.${name} is declared static and does not exist`); continue; }
    const d = Object.getOwnPropertyDescriptor(cls, name);
    if (typeof d.value === 'function') compareParams(`${where}.${name}`, overloads, d.value);
  }
  for (const name of staticNames) {
    checked++;
    if (!declared.static.has(name) && !(internal.static ?? []).includes(name) && !hidden(name)) {
      fail(`${where}.${name} is public and not declared (declare it, or list it in INTERNAL with a reason)`);
    }
  }
  // Instance members: the prototype chain plus the fields a real instance has.
  const protoNames = prototypeNames(cls);
  const own = new Set(Object.keys(sample));
  for (const [name, overloads] of declared.instance) {
    checked++;
    if (!protoNames.has(name) && !own.has(name)) { fail(`${where}#${name} is declared and does not exist`); continue; }
    if (protoNames.has(name)) {
      let p = cls.prototype;
      let d;
      while (p && !(d = Object.getOwnPropertyDescriptor(p, name))) p = Object.getPrototypeOf(p);
      if (typeof d.value === 'function') compareParams(`${where}#${name}`, overloads, d.value);
    }
  }
  for (const name of new Set([...protoNames, ...own])) {
    checked++;
    if (!declared.instance.has(name) && !(internal.instance ?? []).includes(name) && !hidden(name)) {
      fail(`${where}#${name} is public and not declared (declare it, or list it in INTERNAL with a reason)`);
    }
  }
  for (const side of ['static', 'instance']) {
    for (const name of internal[side] ?? []) {
      checked++;
      if (declared[side].has(name)) fail(`${where}: ${name} is listed as INTERNAL and also declared`);
    }
  }
  if (declared.ctor !== undefined) compareParams(`${where} constructor`, [declared.ctor], cls);
}

function compareModule(file, mod, samples) {
  const decl = readDeclarations(file);
  const runtime = new Set(Object.keys(mod));
  for (const [name, what] of decl.values) {
    checked++;
    if (!runtime.has(name)) { fail(`${file}: ${name} is declared and not exported`); continue; }
    if (Array.isArray(what)) compareParams(`${file}: ${name}`, what, mod[name]);
  }
  for (const name of runtime) {
    checked++;
    if (!decl.values.has(name)) fail(`${file}: ${name} is exported and not declared`);
  }
  for (const [name, members] of decl.classes) {
    if (!runtime.has(name)) continue;
    if (!(name in samples)) { fail(`${file}: no sample instance of ${name} to compare with`); continue; }
    compareClass(`${file}: ${name}`, mod[name], members, samples[name](), INTERNAL[name]);
  }
  for (const [name, members] of decl.namespaces) {
    if (!runtime.has(name)) continue;
    const ns = mod[name];
    for (const [member, overloads] of members) {
      checked++;
      if (!(member in ns)) { fail(`${file}: ${name}.${member} is declared and does not exist`); continue; }
      if (overloads[0] !== null) compareParams(`${file}: ${name}.${member}`, overloads, ns[member]);
    }
    for (const member of Object.keys(ns)) {
      checked++;
      if (!members.has(member) && !(INTERNAL[name]?.members ?? []).includes(member)) {
        fail(`${file}: ${name}.${member} is exported and not declared`);
      }
    }
  }
}

// --- the samples ----------------------------------------------------------

const { Value, Program, SelError, compile } = sel;
const { Binding, Bindings, Fragment, HybridPlan, JoinPlan, RelationalPlan, Sql, SqlError } = sql;

compareModule('js/src/sel.d.ts', sel, {
  Value: () => Value.list([Value.text('a')]),
  Program: () => compile('1'),
  SelError: () => new SelError('E_X', 'x', null),
  ...(sel.RecordShape ? { RecordShape: () => new sel.RecordShape(['a']) } : {}),
});

const bindings = { T: Binding.relation('t', null, { X: Binding.column('x', 't', 'NUM') }) };
compareModule('js/src/sql.d.ts', sql, {
  SqlError: () => new SqlError('E_X', 'x', null),
  Fragment: () => Sql.translate(compile('X > 1'), 'sqlite', { X: Binding.column('x', null, 'NUM') }),
  Binding: () => Binding.column('x'),
  Bindings: () => new Bindings({}),
  JoinPlan: () => new JoinPlan(),
  RelationalPlan: () => new RelationalPlan(),
  HybridPlan: () => Sql.planHybrid(compile('T .> FILTER(_["X"] > 1)'), 'sqlite', bindings),
  Sql: () => ({}),
});

console.log(`check-js-dts: ${checked - failures} of ${checked} declarations agree`);
process.exit(failures ? 1 : 0);
