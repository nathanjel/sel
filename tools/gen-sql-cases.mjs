#!/usr/bin/env node
// sql/cases/*.sqlt -> per-host case data, as code.
//
// The same rule the dialect map follows, applied to the test fixtures: one
// source, read by one program, emitted as initialisers each host loads. No host
// parses anything.
//
// It exists because the alternative was two hand-written readers. sql/cases
// spells its bindings as JSON, and PHP's json_decode represents a JSON object
// and a JSON array as the same type while Python's tells them apart -- so the
// two runners disagreed about what `items` as an object meant, and the mapping
// had to be patched in two places to agree. That is the defect the whole
// cross-host review was about, reproduced in the harness by the person fixing
// it. One reader cannot disagree with itself.
//
// A binding is emitted as a CONSTRUCTOR CALL, never as data:
//
//     {"kind": "column", "table": "o", "column": "total", "type": "NUM"}
//       php     Binding::column('total', 'o', 'NUM')
//       python  Binding.column('total', 'o', 'NUM')
//
// and an unrecognised shape is emitted faithfully rather than fixed, because
// several cases exist to assert that a malformed binding is REFUSED. The
// constructors do the refusing, identically, in every host -- which is the
// point of them.
//
//   node tools/gen-sql-cases.mjs            write the files
//   node tools/gen-sql-cases.mjs --check    verify they are current

import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SUITE = resolve(ROOT, 'sql/cases');

const SECTIONS = ['dialect', 'register', 'bindings', 'options', 'as', 'mode',
                  'source', 'expect', 'params', 'error', 'throws'];

const errors = [];
const fail = (where, msg) => errors.push(`${where}: ${msg}`);

// --- the .sqlt format -------------------------------------------------------
//
// Transcribed from php/bin/sqlt-format.php, which this replaces. The header
// grammar is `### name: <one token>`; `===` closes a case; `--- <section>`
// opens one; `--- note` is prose and is dropped.

function parseSqlt(text, file) {
  const cases = [];
  let cur = -1;
  let section = null;
  text.split('\n').forEach((line, idx) => {
    const at = `${file}:${idx + 1}`;
    if (line.startsWith('### ')) {
      const m = /^###\s+name:\s*(\S+)\s*$/.exec(line);
      if (!m) { fail(at, 'malformed case header'); return; }
      const c = { name: m[1], at };
      for (const s of SECTIONS) c[s] = null;
      cases.push(c);
      cur = cases.length - 1;
      section = null;
      return;
    }
    if (line === '===') { cur = -1; section = null; return; }
    if (line.startsWith('--- ')) {
      if (cur < 0) { fail(at, 'section outside a case'); return; }
      section = line.slice(4).trim();
      if (SECTIONS.includes(section)) cases[cur][section] = [];
      else if (section !== 'note') fail(at, `unknown section ${section}`);
      return;
    }
    if (cur < 0 || section === null || section === 'note') return;
    cases[cur][section].push(line);
  });

  for (const c of cases) {
    for (const s of SECTIONS) {
      if (c[s] !== null) c[s] = c[s].join('\n').trim();
    }
    if (c.source === null) fail(c.at, `case ${c.name} has no --- source`);
    const outcomes = [c.expect, c.error, c.throws].filter((x) => x !== null);
    if (outcomes.length !== 1) {
      fail(c.at, `case ${c.name} needs exactly one of --- expect, --- error and --- throws`);
    }
    if (!c.dialect) fail(c.at, `case ${c.name} has no --- dialect`);
  }
  return cases;
}

function decodeJson(text, what, at) {
  if (text === null || text === '') return null;
  try { return JSON.parse(text); }
  catch (e) { fail(at, `--- ${what} is not JSON: ${e.message}`); return null; }
}

// --- bindings: JSON -> constructor calls -------------------------------------

const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

// A Call is emitted as `Binding::name(a, b)` in PHP and `Binding.name(a, b)` in
// Python. Arguments are either literal values or nested Calls.
const call = (name, args) => ({ __call: name, args });
const raw = (php, py) => ({ __raw: true, php, py });

function bindingCall(b, where) {
  if (!isObj(b) || b.kind === undefined) {
    fail(where, 'a binding needs a kind');
    return call('column', ['?']);
  }
  const type = b.type === undefined ? 'UNKNOWN' : b.type;
  switch (b.kind) {
    case 'column':
      return b.raw !== undefined
        ? call('raw', [b.raw, type])
        : call('column', [b.column === undefined ? null : b.column,
                          b.table === undefined ? null : b.table, type]);

    case 'columns': {
      // JS can see the difference PHP's decoder cannot, so it is decided here.
      // A columns binding is indexed by position, so its items are a JSON array;
      // anything else is passed through as one argument and the constructor
      // refuses it, which is what the case asserting that expects.
      if (!Array.isArray(b.items)) return call('columns', [b.items]);
      return call('columns', b.items.map((i) => bindingCall({ kind: 'column', ...i }, where)));
    }

    case 'relation': {
      const fields = b.fields === undefined ? {} : b.fields;
      let builtFields;
      if (isObj(fields)) {
        builtFields = {};
        for (const [k, spec] of Object.entries(fields)) {
          builtFields[k] = bindingCall({ kind: 'column', ...spec }, where);
        }
      } else {
        builtFields = fields;                       // refused by the constructor
      }
      const corr = isObj(b.correlate) && b.correlate.raw !== undefined
        ? b.correlate.raw
        : (b.correlate === undefined ? null : b.correlate);
      const rest = [b.alias === undefined ? null : b.alias, builtFields,
                    b.scalar === undefined ? null : b.scalar, corr];
      const from = b.from;
      if (isObj(from)) {
        return call('relationQuery', [from.raw === undefined ? from : from.raw, ...rest]);
      }
      return call('relation', [from === undefined ? null : from, ...rest]);
    }

    case 'value':
      return call('value', [valueCall(b.value, where), b.type === undefined ? null : b.type]);
  }
  fail(where, `unknown binding kind ${b.kind}`);
  return call('column', ['?']);
}

/**
 * A JSON value -> a Value constructor call.
 *
 * A JSON NUMBER is refused, here, at generation time -- the one place that reads
 * JSON at all. PHP's decoder turns a 20-digit integer into a float, Python keeps
 * it exact, and JS cannot tell 1.0 from 1, so no decoding rule is implementable
 * in all six hosts. Test data spells its numbers as strings and declares
 * `"type": "NUM"` when it wants them unquoted, which is exactly what the library
 * asks of an application.
 */
function valueCall(v, where) {
  if (typeof v === 'number') {
    fail(where, `write ${v} as a string and declare "type": "NUM"; a JSON number `
              + 'does not survive every host');
    return raw('Value::none()', 'Value.none()');
  }
  if (v === null || v === undefined) return raw('Value::none()', 'Value.none()');
  if (typeof v === 'boolean') return raw(`Value::bool(${v})`, `Value.bool(${v ? 'True' : 'False'})`);
  if (typeof v === 'string') return raw(`Value::text(${phpStr(v)})`, `Value.text(${pyStr(v)})`);
  if (isObj(v) && Object.keys(v).length === 1 && typeof v.bin === 'string') {
    // JSON has no byte string, so the corpus spells one as {"bin": "<hex>"}.
    if (!/^([0-9a-fA-F]{2})*$/.test(v.bin)) fail(where, `the bin value ${v.bin} is not hex`);
    return raw(`Value::bin(hex2bin(${phpStr(v.bin)}))`,
               `Value.bin(bytes.fromhex(${pyStr(v.bin)}))`);
  }
  // A list or a map of further values.
  const parts = Array.isArray(v)
    ? v.map((x) => [null, valueCall(x, where)])
    : Object.entries(v).map(([k, x]) => [k, valueCall(x, where)]);
  return raw(
    'sel_value_tree([' + parts.map(([k, x]) =>
      (k === null ? '' : phpStr(k) + ' => ') + emitPhpArg(x)).join(', ') + '])',
    'value_tree([' + parts.map(([k, x]) =>
      (k === null ? '' : '(' + pyStr(k) + ', ') + emitPyArg(x) + (k === null ? '' : ')')).join(', ') + '])');
}

// --- emitters ---------------------------------------------------------------

const phpStr = (s) => "'" + String(s).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + "'";
const pyStr = (s) => JSON.stringify(String(s));

function emitPhpArg(v) {
  if (v === null || v === undefined) return 'null';
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return phpStr(v);
  if (v.__raw) return v.php;
  if (v.__call) return `Binding::${v.__call}(${v.args.map(emitPhpArg).join(', ')})`;
  if (Array.isArray(v)) return '[' + v.map(emitPhpArg).join(', ') + ']';
  const e = Object.entries(v);
  if (!e.length) return '[]';
  return '[' + e.map(([k, x]) => `${phpStr(k)} => ${emitPhpArg(x)}`).join(', ') + ']';
}

function emitPyArg(v) {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return pyStr(v);
  if (v.__raw) return v.py;
  if (v.__call) {
    const name = v.__call === 'relationQuery' ? 'relation_query' : v.__call;
    return `Binding.${name}(${v.args.map(emitPyArg).join(', ')})`;
  }
  if (Array.isArray(v)) return '[' + v.map(emitPyArg).join(', ') + ']';
  const e = Object.entries(v);
  if (!e.length) return '{}';
  return '{' + e.map(([k, x]) => `${pyStr(k)}: ${emitPyArg(x)}`).join(', ') + '}';
}

const BANNER = [
  'GENERATED by tools/gen-sql-cases.mjs from sql/cases/*.sqlt — do not edit.',
  'Regenerate with: node tools/gen-sql-cases.mjs',
  '',
  'Every host loads this rather than parsing the case files, for the same reason',
  'every host loads the generated dialect map rather than the JSON: one reader',
  'cannot disagree with itself, and two did.',
  '',
  'Bindings are constructor calls, built lazily -- a case may assert that',
  'constructing one is refused, so the call has to happen inside the runner\'s',
  'try rather than when this file loads.',
];

function emitPhp(cases) {
  const body = cases.map((c) => {
    const fields = [
      ['name', c.name], ['at', c.at], ['dialect', c.dialect], ['source', c.source],
      ['expect', c.expect], ['error', c.error], ['throws', c.throws],
      ['params', c.params], ['as', c.as], ['mode', c.mode],
      ['register', c.registerData], ['options', c.optionsData],
      ['hasBindings', c.bindings !== null],
    ].map(([k, v]) => `            ${phpStr(k)} => ${emitPhpArg(v)},`).join('\n');
    const binds = Object.entries(c.bindingCalls)
      .map(([n, x]) => `${phpStr(n)} => ${emitPhpArg(x)}`).join(', ');
    return `        [\n${fields}\n            'bindings' => static fn (): array => [${binds}],\n        ],`;
  }).join('\n');
  return `<?php\n// ${BANNER.join('\n// ')}\n\ndeclare(strict_types=1);\n\n`
    + `require_once __DIR__ . '/../src/Sql/bootstrap.php';\n\n`
    + `use Sel\\Sql\\Binding;\nuse Sel\\Value;\n\n`
    + `/** A list of Value children, keyed as SEL keys them. */\n`
    + `function sel_value_tree(array $items): Value\n{\n`
    + `    $v = Value::list([]);\n    $i = 0;\n`
    + `    foreach ($items as $k => $child) {\n`
    + `        $v->set(is_int($k) ? (string) (++$i) : (string) $k, $child);\n`
    + `    }\n    return $v;\n}\n\n`
    + `/**\n`
    + ` * Every case in sql/cases, in file then source order.\n`
    + ` *\n`
    + ` * A function rather than a const because a case carries a closure for its\n`
    + ` * bindings, and PHP constants cannot hold one.\n`
    + ` *\n`
    + ` * @return list<array<string,mixed>>\n`
    + ` */\n`
    + `function sql_cases(): array\n{\n    return [\n${body}\n    ];\n}\n`;
}

function emitPython(cases) {
  const body = cases.map((c) => {
    const fields = [
      ['name', c.name], ['at', c.at], ['dialect', c.dialect], ['source', c.source],
      ['expect', c.expect], ['error', c.error], ['throws', c.throws],
      ['params', c.params], ['as', c.as], ['mode', c.mode],
      ['register', c.registerData], ['options', c.optionsData],
      ['hasBindings', c.bindings !== null],
    ].map(([k, v]) => `        ${pyStr(k)}: ${emitPyArg(v)},`).join('\n');
    const binds = Object.entries(c.bindingCalls)
      .map(([n, x]) => `${pyStr(n)}: ${emitPyArg(x)}`).join(', ');
    return `    {\n${fields}\n        "bindings": lambda: {${binds}},\n    },`;
  }).join('\n');
  return `"""${BANNER.join('\n')}\n"""\n\nfrom __future__ import annotations\n\n`
    + `from sel.sql import Binding\nfrom sel.value import Value\n\n\n`
    + `def value_tree(items):\n`
    + `    """A list of Value children, keyed as SEL keys them."""\n`
    + `    v = Value.list([])\n    i = 0\n`
    + `    for item in items:\n`
    + `        if isinstance(item, tuple):\n`
    + `            v.set(str(item[0]), item[1])\n`
    + `        else:\n`
    + `            i += 1\n            v.set(str(i), item)\n`
    + `    return v\n\n\n`
    + `SQL_CASES = [\n${body}\n]\n`;
}

// --- main -------------------------------------------------------------------

const files = readdirSync(SUITE).filter((f) => f.endsWith('.sqlt')).sort();
const cases = [];
const seen = new Map();
for (const f of files) {
  for (const c of parseSqlt(readFileSync(resolve(SUITE, f), 'utf8'), basename(f))) {
    if (seen.has(c.name)) fail(c.at, `two cases are named ${c.name}: ${seen.get(c.name)} and ${c.at}`);
    seen.set(c.name, c.at);
    const bindings = decodeJson(c.bindings, 'bindings', c.at);
    c.bindingCalls = {};
    if (bindings !== null) {
      if (!isObj(bindings)) fail(c.at, '--- bindings is not a JSON object');
      else for (const [n, b] of Object.entries(bindings)) {
        c.bindingCalls[n] = bindingCall(b, `${c.at}: binding ${n}`);
      }
    }
    c.registerData = decodeJson(c.register, 'register', c.at);
    if (c.register !== null && !Array.isArray(c.registerData)) {
      fail(c.at, '--- register is not a JSON list');
    }
    c.optionsData = decodeJson(c.options, 'options', c.at);
    cases.push(c);
  }
}

if (errors.length) {
  for (const e of errors) process.stderr.write(`${e}\n`);
  process.stderr.write(`\n${errors.length} problem(s) in sql/cases\n`);
  process.exit(1);
}

const OUTPUTS = [['php/bin/CaseData.php', emitPhp], ['python/bin/case_data.py', emitPython]];
const check = process.argv.includes('--check');
let stale = 0;
for (const [rel, emit] of OUTPUTS) {
  const path = resolve(ROOT, rel);
  const text = emit(cases);
  if (check) {
    let have = null;
    try { have = readFileSync(path, 'utf8'); } catch { /* absent counts as stale */ }
    if (have !== text) { process.stderr.write(`stale: ${rel}\n`); stale++; }
  } else {
    writeFileSync(path, text);
    process.stdout.write(`wrote ${rel}\n`);
  }
}
if (check) {
  if (stale) { process.stderr.write('\nrun: node tools/gen-sql-cases.mjs\n'); process.exit(1); }
  process.stdout.write(`sql cases are current — ${cases.length} case(s)\n`);
}
