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

import { readFileSync, readdirSync } from 'node:fs';
import { genArgs, writeOrCheck, jsStr, pyStr, phpStr, lispStr, goStr, rustStr } from './gen-lib.mjs';
import { Unrepresentable, shapeOf, cppStr, cppName, cppEntrySpec, cppDialectSpec,
         SECTIONS as CPP_SECTIONS }
  from './cpp-emit.mjs';
import { resolve, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SUITE = resolve(ROOT, 'sql/cases');
const USAGE = `usage: node tools/gen-sql-cases.mjs            write the files
       node tools/gen-sql-cases.mjs --check    verify they are current`;
const { check } = genArgs('gen-sql-cases', USAGE);

const SECTIONS = ['dialect', 'register', 'bindings', 'options', 'as', 'mode',
                  'source', 'expect', 'params', 'error', 'throws', 'plan', 'tables'];

// `--- plan` turns a case into a PLANNER case: the runner calls plan_hybrid
// instead of translate and asserts the classification, the physical source
// tables, the SQL prefix and that the caller's AST survived planning. See
// sql/cases/README.md.
const PLANS = ['pure_sql', 'hybrid', 'pure_memory', 'refused'];

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
    if (c.plan !== null) {
      // A planner case. A pure-memory plan emits no SQL, so it has no
      // `--- expect`; the other two carry the prefix statement. `--- error` is
      // the one refusal planning itself can raise -- a base dialect, an alias
      // collision -- and a plan is then not classified at all.
      if (!PLANS.includes(c.plan)) {
        fail(c.at, `case ${c.name}: --- plan must be one of ${PLANS.join(', ')}`);
      }
      if (c.throws !== null) fail(c.at, `case ${c.name}: a planner case cannot --- throws`);
      if (c.plan === 'refused') {
        if (c.error === null || c.expect !== null || c.tables !== null) {
          fail(c.at, `case ${c.name}: a refused plan has --- error and nothing else`);
        }
        // Planning refuses on the bindings or the dialect -- a base dialect,
        // an alias collision -- which blame no node of the rule, so every
        // host reports such an error without a position. A position written
        // here would be one no runner can compare (SEL-0046); a refusal that
        // blames a place in the program is a translate case.
        if (c.error !== null && /\s/.test(c.error)) {
          fail(c.at, `case ${c.name}: a refused plan blames the bindings or the dialect, not a position -- write the code alone`);
        }
      } else if (c.error !== null) {
        fail(c.at, `case ${c.name}: a plan that raises is --- plan refused`);
      } else if (c.plan === 'pure_memory' && c.expect !== null) {
        fail(c.at, `case ${c.name}: a pure_memory plan emits no SQL, drop --- expect`);
      } else if (c.plan !== 'pure_memory' && c.expect === null) {
        fail(c.at, `case ${c.name}: a ${c.plan} plan needs --- expect, its SQL prefix`);
      }
      if (c.params !== null) fail(c.at, `case ${c.name}: --- params is not checked for a plan`);
      if (c.as !== null) fail(c.at, `case ${c.name}: a plan is always a statement, drop --- as`);
    } else if (outcomes.length !== 1) {
      fail(c.at, `case ${c.name} needs exactly one of --- expect, --- error and --- throws`);
    }
    if (c.tables !== null && c.plan === null) {
      fail(c.at, `case ${c.name}: --- tables only makes sense with --- plan`);
    }
    // One physical source per line, first-use order. An empty section is a
    // claim that there are none, which is not the same as no claim.
    c.tableList = c.tables === null ? null
      : c.tables === '' ? [] : c.tables.split('\n').map((t) => t.trim()).filter((t) => t !== '');
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
const raw = (php, py, js, cpp, lisp, go, rust) => ({ __raw: true, php, py, js, cpp, lisp, go, rust });

function bindingCall(b, where) {
  if (!isObj(b) || b.kind === undefined) {
    fail(where, 'a binding needs a kind');
    return call('column', ['?']);
  }
  const type = b.type === undefined ? 'UNKNOWN' : b.type;
  const unique = (expr) => b.uniqueKey === undefined ? expr : call('withUniqueKey', [expr, b.uniqueKey]);
  const exact = b.exact === true || b.collation === 'binary' || b.collation === 'exact';
  const sargable = b.sargable === true || b.collation === 'sargable' || b.collation === 'prefilter';
  const guard = b.guard === true;
  let prefilter = b.prefilter;
  if (prefilter === undefined && b.splitSargable !== undefined) {
    prefilter = b.splitSargable ? 'separate' : 'inline';
  }
  if (prefilter === true) prefilter = 'separate';
  if (prefilter === false) prefilter = 'inline';
  const hasFlags = exact || sargable || guard || (prefilter !== undefined);
  const flagArgs = (extra) => (prefilter !== undefined)
    ? [...extra, exact, sargable, guard, null, prefilter]
    : [...extra, exact, sargable, guard];

  switch (b.kind) {
    case 'column': {
      const col = b.column === undefined ? null : b.column;
      const table = b.table === undefined ? null : b.table;
      return b.raw !== undefined
        ? (hasFlags ? call('raw', flagArgs([b.raw, type])) : call('raw', [b.raw, type]))
        : (hasFlags ? call('column', flagArgs([col, table, type]))
                    : call('column', [col, table, type]));
    }
    case 'raw':
      return hasFlags ? call('raw', flagArgs([b.raw, type])) : call('raw', [b.raw, type]);

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
      if (prefilter !== undefined) {
        rest.push(prefilter);
      }
      const from = b.from;
      if (isObj(from)) {
        return unique(call('relationQuery', [from.raw === undefined ? from : from.raw, ...rest]));
      }
      return unique(call('relation', [from === undefined ? null : from, ...rest]));
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
 * in every host. Test data spells its numbers as strings and declares
 * `"type": "NUM"` when it wants them unquoted, which is exactly what the library
 * asks of an application.
 */
function valueCall(v, where) {
  if (typeof v === 'number') {
    fail(where, `write ${v} as a string and declare "type": "NUM"; a JSON number `
              + 'does not survive every host');
    return raw('Value::none()', 'Value.none()', 'Value.none()', 'Value::none()',
               '(sel:make-none)', 'sel.NewNone()', 'Value::none()');
  }
  if (v === null || v === undefined) {
    return raw('Value::none()', 'Value.none()', 'Value.none()', 'Value::none()',
               '(sel:make-none)', 'sel.NewNone()', 'Value::none()');
  }
  if (typeof v === 'boolean') {
    return raw(`Value::bool(${v})`, `Value.bool(${v ? 'True' : 'False'})`,
               `Value.bool(${v})`, `Value::boolean(${v})`,
               `(sel:make-bool ${v ? 't' : 'nil'})`,
               `sel.NewBool(${v})`,
               `Value::bool(${v})`);
  }
  if (typeof v === 'string') {
    return raw(`Value::text(${phpStr(v)})`, `Value.text(${pyStr(v)})`,
               `Value.text(${jsStr(v)})`, `Value::text(${cppStr(v)})`,
               `(sel:make-text ${lispStr(v)})`,
               `sel.NewText(${goStr(v)})`,
               `Value::text_owned(${rustStr(v)}.to_string())`);
  }
  if (isObj(v) && Object.keys(v).length === 1 && typeof v.bin === 'string') {
    // JSON has no byte string, so the corpus spells one as {"bin": "<hex>"}.
    if (!/^([0-9a-fA-F]{2})*$/.test(v.bin)) fail(where, `the bin value ${v.bin} is not hex`);
    return raw(`Value::bin(hex2bin(${phpStr(v.bin)}))`,
               `Value.bin(bytes.fromhex(${pyStr(v.bin)}))`,
               `Value.bin(binFromHex(${jsStr(v.bin)}))`,
               `Value::bin(bin_from_hex(${cppStr(v.bin)}))`,
               `(sel:make-bin (bin-from-hex ${lispStr(v.bin)}))`,
               `binFromHex(${goStr(v.bin)})`,
               `bin_from_hex(${rustStr(v.bin)})`);
  }
  // A list or a map of further values.
  const parts = Array.isArray(v)
    ? v.map((x) => [null, valueCall(x, where)])
    : Object.entries(v).map(([k, x]) => [k, valueCall(x, where)]);
  return raw(
    'sel_value_tree([' + parts.map(([k, x]) =>
      (k === null ? '' : phpStr(k) + ' => ') + emitPhpArg(x)).join(', ') + '])',
    'value_tree([' + parts.map(([k, x]) =>
      (k === null ? '' : '(' + pyStr(k) + ', ') + emitPyArg(x) + (k === null ? '' : ')')).join(', ') + '])',
    'valueTree([' + parts.map(([k, x]) =>
      (k === null ? '' : '[' + jsStr(k) + ', ') + emitJsArg(x) + (k === null ? '' : ']')).join(', ') + '])',
    'value_tree({' + parts.map(([k, x]) =>
      '{' + (k === null ? 'std::nullopt' : cppStr(k)) + ', ' + cppBinding(x) + '}').join(', ') + '})',
    '(value-tree (list ' + parts.map(([k, x]) =>
      '(cons ' + (k === null ? 'nil' : lispStr(k)) + ' ' + lispArg(x) + ')').join(' ') + '))',
    'valueTree([]treeItem{' + parts.map(([k, x]) =>
      '{key: ' + (k === null ? 'nil' : `strPtr(${goStr(k)})`) + ', val: ' + (x.__raw ? x.go : goBinding(x)) + '}').join(', ') + '})',
    'value_tree(vec![' + parts.map(([k, x]) =>
      'TreeItem { key: ' + (k === null ? 'None' : `Some(${rustStr(k)}.to_string())`) + ', val: ' + (x.__raw ? x.rust : rustBinding(x)) + ' }').join(', ') + '])');
}

// --- emitters ---------------------------------------------------------------


function emitPhpArg(v) {
  if (v?.__call === 'withUniqueKey') return `(${emitPhpArg(v.args[0])})->withUniqueKey(${emitPhpArg(v.args[1])})`;
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
  if (v?.__call === 'withUniqueKey') return `${emitPyArg(v.args[0])}.with_unique_key(${emitPyArg(v.args[1])})`;
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

function emitJsArg(v) {
  if (v?.__call === 'withUniqueKey') return `${emitJsArg(v.args[0])}.withUniqueKey(${emitJsArg(v.args[1])})`;
  if (v === null || v === undefined) return 'null';
  if (v === true) return 'true';
  if (v === false) return 'false';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return jsStr(v);
  if (v.__raw) return v.js;
  if (v.__call) return `Binding.${v.__call}(${v.args.map(emitJsArg).join(', ')})`;
  if (Array.isArray(v)) return '[' + v.map(emitJsArg).join(', ') + ']';
  const e = Object.entries(v);
  if (!e.length) return '{}';
  return '{ ' + e.map(([k, x]) => `${jsStr(k)}: ${emitJsArg(x)}`).join(', ') + ' }';
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
      ['plan', c.plan], ['tables', c.tableList],
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
      ['plan', c.plan], ['tables', c.tableList],
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

function emitJs(cases) {
  const body = cases.map((c) => {
    const fields = [
      ['name', c.name], ['at', c.at], ['dialect', c.dialect], ['source', c.source],
      ['expect', c.expect], ['error', c.error], ['throws', c.throws],
      ['params', c.params], ['as', c.as], ['mode', c.mode],
      ['register', c.registerData], ['options', c.optionsData],
      ['plan', c.plan], ['tables', c.tableList],
    ].map(([k, v]) => `    ${jsStr(k)}: ${emitJsArg(v)},`).join('\n');
    const binds = Object.entries(c.bindingCalls)
      .map(([n, x]) => `${jsStr(n)}: ${emitJsArg(x)}`).join(', ');
    return `  {\n${fields}\n    bindings: () => ({ ${binds} }),\n  },`;
  }).join('\n');
  return `// ${BANNER.join('\n// ')}\n\n`
    + `import { Binding } from '../src/sql/index.mjs';\n`
    + `import { Value } from '../src/value.mjs';\n\n`
    + `// A list of Value children, keyed as SEL keys them.\n`
    + `function valueTree(items) {\n`
    + `  const v = Value.list([]);\n  let i = 0;\n`
    + `  for (const item of items) {\n`
    + `    if (Array.isArray(item)) {\n`
    + `      v.set(String(item[0]), item[1]);\n`
    + `    } else {\n`
    + `      i += 1;\n      v.set(String(i), item);\n`
    + `    }\n  }\n  return v;\n}\n\n`
    + `// JSON has no byte string, so the corpus spells one as {"bin": "<hex>"}.\n`
    + `function binFromHex(hex) {\n`
    + `  const out = new Uint8Array(hex.length / 2);\n`
    + `  for (let i = 0; i < out.length; i += 1) {\n`
    + `    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);\n`
    + `  }\n  return out;\n}\n\n`
    + `export const SQL_CASES = [\n${body}\n];\n`;
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

// --- Common Lisp ------------------------------------------------------------
//
// Every case, as a plist, with the `bindings` and `register` blocks emitted as
// CALLS rather than data -- the same rule the other hosts follow, so this runner
// parses nothing either.
//
// Unlike C++, nothing here is unrepresentable. Lisp's constructors take whatever
// they are handed and refuse it at run time, exactly as Python's and PHP's do,
// so a case whose point is a malformed binding still runs as a case.

// Function declarations, not consts: valueCall runs while the case files are
// being parsed, which is before this section of the module is evaluated, and a
// const would still be in its temporal dead zone. (The string escapers are
// imported from gen-lib.mjs, and imports are bound before the module runs.)
function lispOpt(v) { return v === null || v === undefined ? 'nil' : lispStr(v); }

const LISP_KIND = { NUM: ':num', TEXT: ':text', BOOL: ':bool', BIN: ':bin',
                    UNKNOWN: ':unknown', LIST: ':list' };

function lispKind(t) {
  if (t === null || t === undefined) return 'nil';
  // Not a kind at all: hand it through as a string and let the constructor
  // refuse it, which is what the dynamic hosts do.
  return LISP_KIND[t] ?? lispStr(t);
}

function lispArg(v) {
  if (v?.__call === 'withUniqueKey') return `(sel.sql:binding-with-unique-key ${lispArg(v.args[0])} ${lispArg(v.args[1])})`;
  if (v === null || v === undefined) return 'nil';
  if (v === true) return 't';
  if (v === false) return 'nil';
  if (typeof v === 'number') return String(v);
  if (typeof v === 'string') return lispStr(v);
  if (v.__raw) return v.lisp;
  if (v.__call) {
    switch (v.__call) {
      case 'column': {
        const [col, table, type, exact, sargable, guard, collation, prefilter] = v.args;
        // lispArg, not lispOpt: a JSON array or object here is the POINT of
        // several cases, and stringifying it would hand the constructor
        // "[object Object]" instead of the shape it is meant to refuse.
        const flags = (exact || sargable || guard || (prefilter !== undefined && prefilter !== null))
          ? ` :exact ${exact ? 't' : 'nil'} :sargable ${sargable ? 't' : 'nil'} :guard ${guard ? 't' : 'nil'}`
            + ((prefilter !== undefined && prefilter !== null) ? ` :prefilter ${lispStr(prefilter)}` : '')
          : '';
        return `(binding-column ${lispArg(col)} ${lispArg(table)} ${lispKind(type)}${flags})`;
      }
      case 'raw': {
        const [sql, type, exact, sargable, guard, collation, prefilter] = v.args;
        const flags = (exact || sargable || guard || (prefilter !== undefined && prefilter !== null))
          ? ` :exact ${exact ? 't' : 'nil'} :sargable ${sargable ? 't' : 'nil'} :guard ${guard ? 't' : 'nil'}`
            + ((prefilter !== undefined && prefilter !== null) ? ` :prefilter ${lispStr(prefilter)}` : '')
          : '';
        return `(binding-raw ${lispArg(sql)} ${lispKind(type)}${flags})`;
      }
      case 'columns':
        return `(binding-columns ${v.args.map(lispArg).join(' ')})`;
      case 'value': {
        const [val, type] = v.args;
        return `(binding-value ${lispArg(val)} ${lispKind(type)})`;
      }
      case 'relation':
      case 'relationQuery': {
        const [from, alias, fields, scalar, corr, prefilter] = v.args;
        const ctor = v.__call === 'relation' ? 'binding-relation' : 'binding-relation-query';
        const f = (fields && typeof fields === 'object' && !Array.isArray(fields)
                   && !fields.__call && !fields.__raw)
          ? '(list ' + Object.entries(fields)
              .map(([k, b]) => `(cons ${lispStr(k)} ${lispArg(b)})`).join(' ') + ')'
          : lispArg(fields);
        const pref = (prefilter !== undefined && prefilter !== null) ? ` :prefilter ${lispStr(prefilter)}` : '';
        return `(${ctor} ${lispArg(from)} ${lispArg(alias)} ${f} ${lispArg(scalar)} ${lispArg(corr)}${pref})`;
      }
    }
  }
  if (Array.isArray(v)) return '(list ' + v.map(lispArg).join(' ') + ')';
  return '(list ' + Object.entries(v).map(([k, x]) =>
    `(cons ${lispStr(k)} ${lispArg(x)})`).join(' ') + ')';
}

function lispEntrySpec(entry) {
  if (entry === null) return 'nil';
  if (typeof entry === 'string') return lispStr(entry);
  if (Array.isArray(entry)) return '(list ' + entry.map(lispArg).join(' ') + ')';
  const parts = [];
  const armed = (o) => '(list ' + Object.entries(o).map(([k, v]) =>
    v === null ? `(cons ${lispStr(k)} nil)` : `(cons ${lispStr(k)} ${lispStr(v)})`).join(' ') + ')';
  for (const [k, v] of Object.entries(entry)) {
    const kw = ':' + k.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
    if ((k === 'tpl' || k === 'variants') && v !== null && typeof v === 'object' && !Array.isArray(v)) {
      parts.push(`${kw} ${armed(v)}`);
    } else if (k === 'arity' && Array.isArray(v) && v.length === 2) {
      parts.push(`${kw} (cons ${lispArg(v[0])} ${lispArg(v[1])})`);
    } else {
      parts.push(`${kw} ${lispArg(v)}`);
    }
  }
  return '(list ' + parts.join(' ') + ')';
}

const LISP_SECTIONS = { ops: ':ops', funcs: ':funcs', skel: ':skel' };

function lispRegister(ops) {
  return ops.map((op) => {
    if (op && typeof op === 'object' && 'define' in op) {
      const a = op.define;
      const [dialect, section, key, entry] = Array.isArray(a) ? a : [a, a, a, a];
      // An unknown section is a case in its own right; keyword-ise it so the
      // registry refuses it by name, as the other hosts do.
      // A STRING for anything that is not one of the three, never a bare
      // keyword token. The Lisp reader UPCASES, so `:OPS` reads as the same
      // symbol `:ops` does and an upper-case section would be ACCEPTED where
      // Python refuses it; and a section containing a space truncates at the
      // space rather than erroring. CHECK-SECTION refuses a string, which is
      // the answer every other host gives.
      const sec = LISP_SECTIONS[section] ?? lispStr(String(section));
      return `      (define-entry ${lispArg(dialect)} ${sec} ${lispArg(key)} ${lispEntrySpec(entry)})`;
    }
    if (op && typeof op === 'object' && 'dialect' in op) {
      const rest = [];
      for (const [k, v] of Object.entries(op)) {
        if (k === 'dialect') continue;
        const kw = ':' + k;
        if (k === 'lexical' && v !== null && typeof v === 'object') {
          rest.push(`${kw} (list ` + Object.entries(v).map(([a, b]) =>
            (b !== null && typeof b === 'object')
              ? `(cons ${lispStr(a)} (list ` + Object.entries(b).map(([x, y]) =>
                  `(cons ${lispStr(x)} ${lispStr(y)})`).join(' ') + '))'
              : `(cons ${lispStr(a)} ${lispArg(b)})`).join(' ') + ')');
        } else {
          rest.push(`${kw} ${lispArg(v)}`);
        }
      }
      return `      (define-dialect ${lispArg(op.dialect)} (list ${rest.join(' ')}))`;
    }
    // A host function (spec §8.1) whose body no case runs.
    if (op && typeof op === 'object' && 'function' in op) {
      const [name, lo, hi] = op.function;
      return `      (sel:register-function ${lispStr(name)} ${lo} ${hi} (lambda (a) (declare (ignore a)) (sel:make-text "")))`;
    }
    return `      (error "a register op needs a dialect, a define or a function")`;
  }).join('\n');
}

function emitLispCases(cases) {
  const body = cases.map((c) => {
    const binds = Object.entries(c.bindingCalls)
      .map(([n, x]) => `(cons ${lispStr(n)} ${lispArg(x)})`).join(' ');
    const reg = (c.registerData === null || c.registerData === undefined)
      ? 'nil'
      : `(lambda ()\n${lispRegister(c.registerData)})`;
    return `  (list
   :name ${lispStr(c.name)}
   :at ${lispStr(c.at)}
   :dialect ${lispOpt(c.dialect)}
   :source ${lispOpt(c.source)}
   :expect ${lispOpt(c.expect)}
   :error ${lispOpt(c.error)}
   :throws ${lispOpt(c.throws)}
   :params ${lispOpt(c.params)}
   :as ${lispOpt(c.as)}
   :mode ${lispOpt(c.mode)}
   :strict ${c.optionsData && c.optionsData.strict ? 't' : 'nil'}
   :plan ${lispOpt(c.plan)}
   :tables ${c.tableList === null ? ':none' : '(list ' + c.tableList.map(lispStr).join(' ') + ')'}
   :register ${reg}
   :bindings (lambda () (list ${binds})))`;
  }).join('\n');

  return `;;;; ${BANNER.join('\n;;;; ')}
;;;;
;;;; Harness, not library: this lives in bin/ because it is test data.
;;;;
;;;; The bindings and register blocks are CALLS, not data, so this runner
;;;; parses nothing -- the same rule every other host follows. Unlike the C++
;;;; table nothing here is unrepresentable: Lisp's constructors take what they
;;;; are handed and refuse it at run time, so a case whose whole point is a
;;;; malformed binding still runs as a case.

(in-package #:sel-sqlt)

(defun bin-from-hex (hex)
  (let ((out (make-array (floor (length hex) 2) :element-type '(unsigned-byte 8))))
    (loop for i from 0 below (length out)
          do (setf (aref out i) (parse-integer hex :start (* 2 i) :end (+ 2 (* 2 i))
                                                   :radix 16)))
    out))

(defun value-tree (items)
  "A list of values, keyed as SEL keys them: an item with no key takes the next
1-based position, and a keyed one takes its key."
  (let ((v (sel:make-list-value '())) (i 0))
    (dolist (cell items v)
      (sel:value-set v (or (car cell) (princ-to-string (incf i))) (cdr cell)))))

(defparameter +sql-cases+
 (list
${body}))
`;
}

// --- C++ --------------------------------------------------------------------
//
// The three dynamic hosts get their language's own literal for `register` and
// `options`, and destructure it in the runner. C++ has no map literal and, more
// to the point, no runtime shapes: Section is an enum, a DialectSpec is made by
// root() or extending(), an EntrySpec by a named constructor. So this emitter
// renders those blocks as TYPED CALLS -- `Map::define(d, Section::Funcs, k,
// EntrySpec::tpl(...))` -- which is the same rule the map itself follows, and
// means the C++ runner parses nothing either.
//
// Some cases cannot be written that way, and that is the interesting part. A
// case asserting that `{"kind": "column", "column": ["a", "b"]}` is refused
// depends on a host whose constructor can RECEIVE an array; C++'s cannot be
// handed one. The refusal moves from run time to compile time, which is a
// stronger guarantee than the case asked for -- so the case is emitted as
// `unrepresentable` with the reason, and the runner reports it as refused by
// the type system rather than skipping it. A case that is unrepresentable and
// expects SUCCESS would be a real gap, and fails generation instead.

// Binding-specific, so not in cpp-emit.mjs: only a case's `--- bindings` block
// names a kind, and only there can one be absent.
function cppKind(t) {
  if (t === null || t === undefined) return 'SqlKind::Unknown';
  if (typeof t !== 'string') throw new Unrepresentable(`a binding type that is ${shapeOf(t)}`);
  const k = { NUM: 'Num', TEXT: 'Text', BOOL: 'Bool', BIN: 'Bin',
              UNKNOWN: 'Unknown', LIST: 'List' }[t];
  if (!k) throw new Unrepresentable(`the binding type ${JSON.stringify(t)}`);
  return `SqlKind::${k}`;
}

function cppOptName(v, what) {
  if (v === null || v === undefined) return 'std::nullopt';
  return cppName(v, what);
}

// A binding call tree -> a typed constructor call.
function cppBinding(v) {
  if (v?.__call === 'withUniqueKey') return `${cppBinding(v.args[0])}.with_unique_key(${cppName(v.args[1], 'a unique key')})`;
  if (v === null || v === undefined || typeof v === 'string') {
    throw new Unrepresentable(`a binding that is ${shapeOf(v)}`);
  }
  if (v.__raw) return v.cpp;
  if (!v.__call) throw new Unrepresentable(`a binding shape that is ${shapeOf(v)}`);

  switch (v.__call) {
    case 'column': {
      const [col, table, type, exact, sargable, guard, collation, prefilter] = v.args;
      const base = `Binding::column(${cppName(col, 'a column name')}, `
           + `${cppOptName(table, 'a table name')}, ${cppKind(type)}`;
      if (exact !== undefined || sargable !== undefined || guard !== undefined || prefilter !== undefined) {
        const pref = (prefilter !== undefined && prefilter !== null) ? cppStr(prefilter) : 'std::nullopt';
        return `${base}, ${exact ? 'true' : 'false'}, ${sargable ? 'true' : 'false'}, ${guard ? 'true' : 'false'}, ${pref})`;
      }
      return `${base})`;
    }
    case 'raw': {
      const [sql, type, exact, sargable, guard, collation, prefilter] = v.args;
      const base = `Binding::raw(${cppName(sql, 'a raw column')}, ${cppKind(type)}`;
      if (exact !== undefined || sargable !== undefined || guard !== undefined || prefilter !== undefined) {
        const pref = (prefilter !== undefined && prefilter !== null) ? cppStr(prefilter) : 'std::nullopt';
        return `${base}, ${exact ? 'true' : 'false'}, ${sargable ? 'true' : 'false'}, ${guard ? 'true' : 'false'}, ${pref})`;
      }
      return `${base})`;
    }
    case 'columns':
      return `Binding::columns({${v.args.map(cppBinding).join(', ')}})`;
    case 'relation':
    case 'relationQuery': {
      const [from, alias, fields, scalar, corr, prefilter] = v.args;
      const ctor = v.__call === 'relation' ? 'relation' : 'relation_query';
      if (fields === null || fields === undefined || Array.isArray(fields)
          || typeof fields !== 'object' || fields.__call || fields.__raw) {
        throw new Unrepresentable(`relation fields that are ${shapeOf(fields)}`);
      }
      const f = Object.entries(fields)
        .map(([k, b]) => `{${cppStr(k)}, ${cppBinding(b)}}`).join(', ');
      const pref = (prefilter !== undefined && prefilter !== null) ? `, ${cppStr(prefilter)}` : '';
      return `Binding::${ctor}(${cppName(from, 'a relation source')}, `
           + `${cppOptName(alias, 'a relation alias')}, {${f}}, `
           + `${cppOptName(scalar, 'a relation scalar')}, `
           + `${cppOptName(corr, 'a relation correlate')}${pref})`;
    }
    case 'value': {
      const [val, type] = v.args;
      const t = type === null || type === undefined ? 'std::nullopt' : cppKind(type);
      return `Binding::value(${cppBinding(val)}, ${t})`;
    }
  }
  throw new Unrepresentable(`the binding constructor ${v.__call}`);
}

// --- register ---------------------------------------------------------------

function cppRegister(ops) {
  return ops.map((op) => {
    if (op === null || typeof op !== 'object') {
      throw new Unrepresentable(`a register op that is ${shapeOf(op)}`);
    }
    if ('define' in op) {
      const a = op.define;
      if (!Array.isArray(a) || a.length !== 4) {
        throw new Unrepresentable(`a define that is ${shapeOf(a)}`);
      }
      const [dialect, section, key, entry] = a;
      const s = CPP_SECTIONS[section];
      // Section is an enum, so an unknown one has no spelling at all.
      if (!s) throw new Unrepresentable(`the map section ${JSON.stringify(section)}`);
      return `      Map::define(${cppName(dialect, 'a dialect')}, Section::${s}, `
           + `${cppName(key, 'an entry key')}, ${cppEntrySpec(entry)});`;
    }
    if ('dialect' in op) {
      return `      Map::define_dialect(${cppName(op.dialect, 'a dialect name')}, `
           + `${cppDialectSpec(op)});`;
    }
    // A host function (spec §8.1) whose body no case runs.
    if ('function' in op) {
      const f = op.function;
      if (!Array.isArray(f) || f.length !== 3 || typeof f[0] !== 'string'
          || !Number.isInteger(f[1]) || !Number.isInteger(f[2])) {
        throw new Unrepresentable(`a function op that is ${shapeOf(f)}`);
      }
      return `      sel::register_function(${cppStr(f[0])}, ${f[1]}, ${f[2]}, `
           + '[](sel::HostArgs&) { return sel::Value::text(""); });';
    }
    throw new Unrepresentable('a register op with neither define, dialect nor function');
  }).join('\n');
}

// --- the emitter ------------------------------------------------------------

function emitCpp(cases) {
  const bodies = [];
  const rows = [];

  cases.forEach((c, i) => {
    let unrep = null;
    let binds = '';
    let reg = '';
    try {
      binds = Object.entries(c.bindingCalls)
        .map(([n, x]) => `      {${cppStr(n)}, ${cppBinding(x)}},`).join('\n');
    } catch (e) {
      if (!(e instanceof Unrepresentable)) throw e;
      unrep = e.why;
    }
    if (unrep === null && c.registerData !== null && c.registerData !== undefined) {
      try {
        reg = cppRegister(c.registerData);
      } catch (e) {
        if (!(e instanceof Unrepresentable)) throw e;
        unrep = e.why;
      }
    }

    // A case this host cannot express must be one the others REFUSE. If it
    // expected a translation, the type system has removed coverage rather than
    // strengthened it, and that is a gap rather than a stronger guarantee.
    if (unrep !== null && !c.error && !c.throws) {
      throw new Error(
        `${c.at}: case ${c.name} cannot be written with the C++ constructors `
        + `(${unrep}) and does not assert a refusal, so C++ would lose the coverage `
        + 'rather than move it to compile time. fail() is not used here: the error '
        + 'list is checked before the emitters run, so a fail() from an emitter is '
        + 'recorded and never read.');
    }

    const fn = `c${i}`;
    if (unrep === null) {
      bodies.push(`static std::vector<std::pair<std::string, Binding>> ${fn}_bind() {\n`
                + `  return {\n${binds}\n  };\n}`);
      if (reg) bodies.push(`static void ${fn}_reg() {\n${reg}\n}`);
    }

    const f = [
      `.name = ${cppStr(c.name)}`,
      `.at = ${cppStr(c.at)}`,
      `.dialect = ${cppStr(c.dialect ?? '')}`,
      `.source = ${cppStr(c.source ?? '')}`,
      `.expect = ${c.expect === null || c.expect === undefined ? 'nullptr' : cppStr(c.expect)}`,
      `.error = ${c.error === null || c.error === undefined ? 'nullptr' : cppStr(c.error)}`,
      `.throws = ${c.throws === null || c.throws === undefined ? 'nullptr' : cppStr(c.throws)}`,
      `.params = ${c.params === null || c.params === undefined ? 'nullptr' : cppStr(c.params)}`,
      `.as_ = ${c.as === null || c.as === undefined ? 'nullptr' : cppStr(c.as)}`,
      `.mode = ${c.mode === null || c.mode === undefined ? 'nullptr' : cppStr(c.mode)}`,
      `.strict = ${!!(c.optionsData && c.optionsData.strict)}`,
      `.plan = ${c.plan === null ? 'nullptr' : cppStr(c.plan)}`,
      `.has_tables = ${c.tableList !== null}`,
      `.tables = {${(c.tableList ?? []).map(cppStr).join(', ')}}`,
      `.unrepresentable = ${unrep === null ? 'nullptr' : cppStr(unrep)}`,
      `.register_fn = ${unrep === null && reg ? `${fn}_reg` : 'nullptr'}`,
      `.bindings_fn = ${unrep === null ? `${fn}_bind` : 'nullptr'}`,
    ];
    rows.push(`    {${f.join(',\n     ')}},`);
  });

  return `// ${BANNER.join('\n// ')}\n`
    + `//\n`
    + `// Bindings and registrations are emitted as TYPED CALLS, not as data: C++\n`
    + `// has no map literal, and more to the point no runtime shapes -- Section is\n`
    + `// an enum and an EntrySpec is made by a named constructor. So this runner\n`
    + `// parses nothing, exactly as the other three do not.\n`
    + `//\n`
    + `// A case whose binding or registration cannot be SPELLED with those\n`
    + `// constructors carries \`unrepresentable\` instead of the two functions. It\n`
    + `// is not skipped: the case asserts that the shape is refused, and here it is\n`
    + `// refused by the compiler, which is the same answer one stage earlier.\n\n`
    + `#include "case_data.hpp"\n\n`
    + `namespace sel::sqlt {\n\n`
    + `using sel::Value;\n\n`
    + `${bodies.join('\n\n')}\n\n`
    + `static const SqlCase CASES[] = {\n${rows.join('\n')}\n};\n\n`
    + `std::span<const SqlCase> sql_cases() { return CASES; }\n\n`
    + `}  // namespace sel::sqlt\n`;
}

// --- Go ---------------------------------------------------------------------

const GO_ENTRY_FIELDS = ['args', 'tpl', 'variants', 'ret', 'caveat', 'since', 'arity', 'builder'];

// The Go rendering is written in gofmt's layout, so the bytes do not depend on
// whether gofmt is on PATH: a multi-line composite literal of key: value lines
// has its values aligned one space past the longest key (every key here is
// short and every value fits on its line, so gofmt never breaks the column),
// an empty one is `{}`, and an empty comment line is a bare `//`.
function goKeyed(pairs, indent) {
  if (pairs.length === 0) return '{}';
  const w = Math.max(...pairs.map(([k]) => [...k].length)) + 1;
  const pad = (k) => k + ':' + ' '.repeat(w - [...k].length);
  return '{\n' + pairs.map(([k, v]) => `${indent}\t${pad(k)}${v},`).join('\n') + `\n${indent}}`;
}


function goOptStr(v) {
  if (v === null || v === undefined) return 'nil';
  return `strPtr(${goStr(v)})`;
}

function goName(v, what) {
  if (typeof v !== 'string') throw new Unrepresentable(`${what} that is ${shapeOf(v)}`);
  return goStr(v);
}
function goOptName(v, what) {
  if (v === null || v === undefined) return '""';
  return goName(v, what);
}

function goKind(t) {
  if (t === null || t === undefined) return 'sql.KindUnknown';
  if (typeof t !== 'string') throw new Unrepresentable(`a binding type that is ${shapeOf(t)}`);
  const k = { NUM: 'KindNum', TEXT: 'KindText', BOOL: 'KindBool', BIN: 'KindBin',
              UNKNOWN: 'KindUnknown', LIST: 'KindList' }[t];
  if (!k) throw new Unrepresentable(`the binding type ${JSON.stringify(t)}`);
  return `sql.${k}`;
}

function goBinding(v) {
  if (v?.__call === 'withUniqueKey') return `${goBinding(v.args[0])}.WithUniqueKey(${goName(v.args[1], 'a unique key')})`;
  if (v === null || v === undefined || typeof v === 'string') {
    throw new Unrepresentable(`a binding that is ${shapeOf(v)}`);
  }
  if (v.__raw) return v.go;
  if (!v.__call) throw new Unrepresentable(`a binding shape that is ${shapeOf(v)}`);

  switch (v.__call) {
    case 'column': {
      const [col, table, type, exact, sargable, guard, collation, prefilter] = v.args;
      const c = goName(col, 'a column name');
      const t = goOptName(table, 'a table name');
      const k = goKind(type);
      const e = exact ? 'true' : 'false';
      const s = sargable ? 'true' : 'false';
      const g = guard ? 'true' : 'false';
      const pref = (prefilter !== undefined && prefilter !== null) ? goStr(prefilter) : '""';
      return `bindCol(${c}, ${t}, ${k}, ${e}, ${s}, ${g}, ${pref})`;
    }
    case 'raw': {
      const [sql, type, exact, sargable, guard, collation, prefilter] = v.args;
      const s = goName(sql, 'a raw column');
      const k = goKind(type);
      const e = exact ? 'true' : 'false';
      const sarg = sargable ? 'true' : 'false';
      const g = guard ? 'true' : 'false';
      const pref = (prefilter !== undefined && prefilter !== null) ? goStr(prefilter) : '""';
      return `bindRaw(${s}, ${k}, ${e}, ${sarg}, ${g}, ${pref})`;
    }
    case 'columns':
      return `bindColumns([]*sql.Binding{${v.args.map(goBinding).join(', ')}})`;
    case 'relation':
    case 'relationQuery': {
      const [from, alias, fields, scalar, corr, prefilter] = v.args;
      const fn = v.__call === 'relation' ? 'bindRelation' : 'bindRelationQuery';
      if (fields === null || fields === undefined || Array.isArray(fields)
          || typeof fields !== 'object' || fields.__call || fields.__raw) {
        throw new Unrepresentable(`relation fields that are ${shapeOf(fields)}`);
      }
      const f = Object.entries(fields)
        .map(([k, b]) => `sql.FieldEntry{Name: ${goStr(k)}, Binding: ${goBinding(b)}}`).join(', ');
      const pref = (prefilter !== undefined && prefilter !== null) ? goStr(prefilter) : '""';
      const sc = (scalar !== undefined && scalar !== null) ? goStr(scalar) : '""';
      const cr = (corr !== undefined && corr !== null) ? goName(corr, 'a relation correlate') : '""';
      const al = goOptName(alias, 'a relation alias');
      const fr = goName(from, 'a relation source');
      return `${fn}(${fr}, ${al}, []sql.FieldEntry{${f}}, ${sc}, ${cr}, ${pref})`;
    }
    case 'value': {
      const [val, type] = v.args;
      const t = (type === null || type === undefined) ? 'nil' : `sqlKindPtr(${goKind(type)})`;
      return `bindValue(${goBinding(val)}, ${t})`;
    }
  }
  throw new Unrepresentable(`the binding constructor ${v.__call}`);
}

function goEntrySpec(entry, section) {
  if (entry === null) return 'nil';
  if (typeof entry === 'string') return goStr(entry);
  if (typeof entry !== 'object' || Array.isArray(entry)) {
    throw new Unrepresentable(`a map entry that is ${shapeOf(entry)}`);
  }
  if (entry.builder !== undefined) {
    throw new Unrepresentable('a builder entry, which no document declares');
  }
  const hasRet = entry.ret !== undefined && entry.ret !== null;
  const parts = [];
  if (entry.variants !== undefined) {
    if (typeof entry.variants !== 'object' || Array.isArray(entry.variants)) {
      throw new Unrepresentable(`variants that are ${shapeOf(entry.variants)}`);
    }
    if (!hasRet) throw new Unrepresentable('variants with no ret');
    const arms = Object.entries(entry.variants).map(([k, t]) => {
      if (t === null) return `${goStr(k)}: nil`;
      if (typeof t !== 'string') throw new Unrepresentable(`a template arm that is ${shapeOf(t)}`);
      return `${goStr(k)}: ${goStr(t)}`;
    }).join(', ');
    parts.push(['"variants"', `map[string]interface{}{${arms}}`]);
  } else if (typeof entry.tpl === 'string') {
    parts.push(['"tpl"', goStr(entry.tpl)]);
  } else if (entry.tpl !== null && typeof entry.tpl === 'object' && !Array.isArray(entry.tpl)) {
    if (!hasRet) throw new Unrepresentable('an arity-keyed template with no ret');
    const arms = Object.entries(entry.tpl).map(([k, t]) => {
      if (t === null) return `${goStr(k)}: nil`;
      if (typeof t !== 'string') throw new Unrepresentable(`a template arm that is ${shapeOf(t)}`);
      return `${goStr(k)}: ${goStr(t)}`;
    }).join(', ');
    parts.push(['"tpl"', `map[string]interface{}{${arms}}`]);
  } else {
    throw new Unrepresentable(`a tpl that is ${shapeOf(entry.tpl)}`);
  }
  if (hasRet) {
    parts.push(['"ret"', goStr(entry.ret)]);
  }
  if (entry.caveat !== undefined && entry.caveat !== null) {
    parts.push(['"caveat"', goName(entry.caveat, 'a caveat')]);
  }
  if (entry.since !== undefined && entry.since !== null) {
    parts.push(['"since"', goName(entry.since, 'a since')]);
  }
  if (entry.arity !== undefined && entry.arity !== null) {
    const a = entry.arity;
    if (!Array.isArray(a) || a.length !== 2 || !a.every((x) => Number.isInteger(x))) {
      throw new Unrepresentable(`an arity that is ${shapeOf(a)} of non-integers`);
    }
    parts.push(['"arity"', `[2]int{${a[0]}, ${a[1]}}`]);
  }
  if (entry.args !== undefined && entry.args !== null) {
    const a = entry.args;
    if (!Array.isArray(a) || !a.every((x) => typeof x === 'string')) {
      throw new Unrepresentable(`args that are ${shapeOf(a)}`);
    }
    parts.push(['"args"', `[]string{${a.map(goStr).join(', ')}}`]);
  }
  for (const k of Object.keys(entry)) {
    if (!GO_ENTRY_FIELDS.includes(k)) throw new Unrepresentable(`the entry field ${JSON.stringify(k)}`);
  }
  return `map[string]interface{}${goKeyed(parts, '\t')}`;
}

function goDialectSpec(doc, allow = []) {
  const known = ['dialect', 'extends', 'version', 'target', 'lexical', ...allow];
  for (const k of Object.keys(doc)) {
    if (!known.includes(k)) throw new Unrepresentable(`a dialect declaration carrying ${JSON.stringify(k)}`);
  }
  if (!('extends' in doc)) throw new Unrepresentable('a dialect declaration with no extends');

  const parts = [];
  if (doc.extends === null) {
    if (typeof doc.version !== 'string') {
      throw new Unrepresentable('a root dialect with no version');
    }
    parts.push(['"extends"', 'nil']);
  } else {
    parts.push(['"extends"', goName(doc.extends, 'an extends')]);
  }
  if (doc.version !== undefined && doc.version !== null) {
    parts.push(['"version"', goName(doc.version, 'a version')]);
  }
  if (doc.target !== undefined && doc.target !== null) {
    if (typeof doc.target !== 'boolean') throw new Unrepresentable(`a target that is ${shapeOf(doc.target)}`);
    parts.push(['"target"', doc.target ? 'true' : 'false']);
  }
  if (doc.lexical !== undefined && doc.lexical !== null) {
    const lex = [];
    for (const [k, v] of Object.entries(doc.lexical)) {
      if (v === null) {
        lex.push(`${goStr(k)}: nil`);
      } else if (typeof v === 'string') {
        lex.push(`${goStr(k)}: ${goStr(v)}`);
      } else if (typeof v === 'object' && !Array.isArray(v)) {
        const sub = [];
        for (const [a, b] of Object.entries(v)) {
          if (typeof b !== 'string') throw new Unrepresentable(`an escape that is ${shapeOf(b)}`);
          sub.push(`${goStr(a)}: ${goStr(b)}`);
        }
        lex.push(`${goStr(k)}: map[string]interface{}{${sub.join(', ')}}`);
      } else {
        throw new Unrepresentable(`a lexical value that is ${shapeOf(v)}`);
      }
    }
    parts.push(['"lexical"', `map[string]interface{}{${lex.join(', ')}}`]);
  }
  return `map[string]interface{}${goKeyed(parts, '\t')}`;
}

function goRegister(ops) {
  return ops.map((op) => {
    if (op === null || typeof op !== 'object') {
      throw new Unrepresentable(`a register op that is ${shapeOf(op)}`);
    }
    if ('define' in op) {
      const a = op.define;
      if (!Array.isArray(a) || a.length !== 4) {
        throw new Unrepresentable(`a define that is ${shapeOf(a)}`);
      }
      const [dialect, section, key, entry] = a;
      if (!['ops', 'funcs', 'skel'].includes(section)) {
        throw new Unrepresentable(`the map section ${JSON.stringify(section)}`);
      }
      return `\tsql.Define(${goName(dialect, 'a dialect')}, ${goStr(section)}, `
           + `${goName(key, 'an entry key')}, ${goEntrySpec(entry, section)})`;
    }
    if ('dialect' in op) {
      return `\tsql.DefineDialect(${goName(op.dialect, 'a dialect name')}, `
           + `${goDialectSpec(op)})`;
    }
    if ('function' in op) {
      const f = op.function;
      if (!Array.isArray(f) || f.length !== 3 || typeof f[0] !== 'string'
          || !Number.isInteger(f[1]) || !Number.isInteger(f[2])) {
        throw new Unrepresentable(`a function op that is ${shapeOf(f)}`);
      }
      return `\tsel.RegisterFunction(${goStr(f[0])}, ${f[1]}, ${f[2]}, `
           + 'func(args *sel.Args) *sel.Value { return sel.NewText("") })';
    }
    throw new Unrepresentable('a register op with neither define, dialect nor function');
  }).join('\n');
}

function emitGo(cases) {
  const bodies = [];
  const rows = [];

  cases.forEach((c, i) => {
    let unrep = null;
    let binds = '';
    let reg = '';
    try {
      binds = goKeyed(Object.entries(c.bindingCalls).map(([n, x]) => [goStr(n), goBinding(x)]), '\t');
    } catch (e) {
      if (!(e instanceof Unrepresentable)) throw e;
      unrep = e.why;
    }
    if (unrep === null && c.registerData !== null && c.registerData !== undefined) {
      try {
        reg = goRegister(c.registerData);
      } catch (e) {
        if (!(e instanceof Unrepresentable)) throw e;
        unrep = e.why;
      }
    }

    if (unrep !== null && !c.error && !c.throws) {
      throw new Error(
        `${c.at}: case ${c.name} cannot be written with the Go constructors `
        + `(${unrep}) and does not assert a refusal, so Go would lose the coverage `
        + 'rather than move it to compile time.');
    }

    const fn = `c${i}`;
    if (unrep === null) {
      bodies.push(`func ${fn}Bind() map[string]*sql.Binding {\n`
                + `\treturn map[string]*sql.Binding${binds}\n}`);
      if (reg) bodies.push(`func ${fn}Reg() {\n${reg}\n}`);
    }

    const tablesStr = c.tableList === null ? 'nil' : `[]string{${(c.tableList ?? []).map(goStr).join(', ')}}`;

    const f = [
      ['Name', goStr(c.name)],
      ['At', goStr(c.at)],
      ['Dialect', goStr(c.dialect ?? '')],
      ['Source', goStr(c.source ?? '')],
      ['Expect', goOptStr(c.expect)],
      ['Error', goOptStr(c.error)],
      ['Throws', goOptStr(c.throws)],
      ['Params', goOptStr(c.params)],
      ['As', goOptStr(c.as)],
      ['Mode', goOptStr(c.mode)],
      ['Strict', c.optionsData && c.optionsData.strict ? 'true' : 'false'],
      ['Plan', goOptStr(c.plan)],
      ['HasTables', c.tableList !== null ? 'true' : 'false'],
      ['Tables', tablesStr],
      ['Unrepresentable', unrep === null ? 'nil' : `strPtr(${goStr(unrep)})`],
      ['RegisterFn', unrep === null && reg ? `${fn}Reg` : 'nil'],
      ['BindingsFn', unrep === null ? `${fn}Bind` : 'nil'],
    ];
    rows.push(`\t${goKeyed(f, '\t')},`);
  });

  return `package main\n\n${BANNER.map((l) => (l ? `// ${l}` : '//')).join('\n')}\n\n`
    + `import (\n`
    + `\t"encoding/hex"\n`
    + `\t"strconv"\n\n`
    + `\t"github.com/nathanjel/sel/go/sel"\n`
    + `\t"github.com/nathanjel/sel/go/sel/sql"\n`
    + `)\n\n`
    + `type treeItem struct {\n\tkey *string\n\tval *sel.Value\n}\n\n`
    + `func strPtr(s string) *string { return &s }\n\n`
    + `func sqlKindPtr(k sql.SqlKind) *sql.SqlKind { return &k }\n\n`
    + `func binFromHex(hexStr string) *sel.Value {\n`
    + `\tb, err := hex.DecodeString(hexStr)\n`
    + `\tif err != nil {\n\t\tpanic(err)\n\t}\n`
    + `\treturn sel.NewBin(b)\n`
    + `}\n\n`
    + `func valueTree(items []treeItem) *sel.Value {\n`
    + `\tv := sel.NewList(nil)\n`
    + `\ti := 0\n`
    + `\tfor _, it := range items {\n`
    + `\t\tk := ""\n`
    + `\t\tif it.key != nil {\n`
    + `\t\t\tk = *it.key\n`
    + `\t\t} else {\n`
    + `\t\t\ti++\n`
    + `\t\t\tk = strconv.Itoa(i)\n`
    + `\t\t}\n`
    + `\t\tv.Set(k, it.val)\n`
    + `\t}\n`
    + `\treturn v\n`
    + `}\n\n`
    + `func bindCol(col, table string, typ sql.SqlKind, exact, sargable, guard bool, prefilter string) *sql.Binding {\n`
    + `\treturn sql.ColumnBinding(col, table, typ, exact, sargable, guard, "", prefilter, false)\n`
    + `}\n\n`
    + `func bindRaw(raw string, typ sql.SqlKind, exact, sargable, guard bool, prefilter string) *sql.Binding {\n`
    + `\treturn sql.RawBinding(raw, typ, exact, sargable, guard, "", prefilter, false)\n`
    + `}\n\n`
    + `func bindColumns(items []*sql.Binding) *sql.Binding {\n`
    + `\treturn sql.ColumnsBinding(items)\n`
    + `}\n\n`
    + `func bindRelation(from, alias string, fields []sql.FieldEntry, scalar, correlate, prefilter string) *sql.Binding {\n`
    + `\treturn sql.RelationBinding(from, alias, fields, scalar, correlate, prefilter, false)\n`
    + `}\n\n`
    + `func bindRelationQuery(query, alias string, fields []sql.FieldEntry, scalar, correlate, prefilter string) *sql.Binding {\n`
    + `\treturn sql.RelationQueryBinding(query, alias, fields, scalar, correlate, prefilter, false)\n`
    + `}\n\n`
    + `func bindValue(val *sel.Value, typ *sql.SqlKind) *sql.Binding {\n`
    + `\treturn sql.ValueBinding(val, typ)\n`
    + `}\n\n`
    + `${bodies.join('\n\n')}\n\n`
    + `type SqlCase struct {\n`
    + `\tName            string\n`
    + `\tAt              string\n`
    + `\tDialect         string\n`
    + `\tSource          string\n`
    + `\tExpect          *string\n`
    + `\tError           *string\n`
    + `\tThrows          *string\n`
    + `\tParams          *string\n`
    + `\tAs              *string\n`
    + `\tMode            *string\n`
    + `\tStrict          bool\n`
    + `\tPlan            *string\n`
    + `\tHasTables       bool\n`
    + `\tTables          []string\n`
    + `\tUnrepresentable *string\n`
    + `\tRegisterFn      func()\n`
    + `\tBindingsFn      func() map[string]*sql.Binding\n`
    + `}\n\n`
    + `var sqlCases = []SqlCase{\n${rows.join('\n')}\n}\n`;
}


function rustOptStr(v) {
  if (v === null || v === undefined) return 'None';
  return `Some(${rustStr(v)})`;
}

function rustName(v, what) {
  if (typeof v !== 'string') throw new Unrepresentable(`${what} that is ${shapeOf(v)}`);
  return rustStr(v);
}

function rustOptName(v, what) {
  if (v === null || v === undefined) return '""';
  return rustName(v, what);
}

function rustKind(t) {
  if (t === null || t === undefined) return 'SqlKind::Unknown';
  if (typeof t !== 'string') throw new Unrepresentable(`a binding type that is ${shapeOf(t)}`);
  const k = { NUM: 'Num', TEXT: 'Text', BOOL: 'Bool', BIN: 'Bin',
              UNKNOWN: 'Unknown', LIST: 'List', STATEMENT: 'Statement' }[t];
  if (!k) throw new Unrepresentable(`the binding type ${JSON.stringify(t)}`);
  return `SqlKind::${k}`;
}

function rustBinding(v) {
  if (v?.__call === 'withUniqueKey') return `${rustBinding(v.args[0])}.with_unique_key(${rustName(v.args[1], 'a unique key')})`;
  if (v === null || v === undefined || typeof v === 'string') {
    throw new Unrepresentable(`a binding that is ${shapeOf(v)}`);
  }
  if (v.__raw) return v.rust || v.go;
  if (!v.__call) throw new Unrepresentable(`a binding shape that is ${shapeOf(v)}`);

  switch (v.__call) {
    case 'column': {
      const [col, table, type, exact, sargable, guard, collation, prefilter] = v.args;
      const c = rustName(col, 'a column name');
      const t = rustOptName(table, 'a table name');
      const k = rustKind(type);
      const e = exact ? 'true' : 'false';
      const s = sargable ? 'true' : 'false';
      const g = guard ? 'true' : 'false';
      const pref = (prefilter !== undefined && prefilter !== null) ? rustStr(prefilter) : '""';
      return `bind_col(${c}, ${t}, ${k}, ${e}, ${s}, ${g}, ${pref})`;
    }
    case 'raw': {
      const [sql, type, exact, sargable, guard, collation, prefilter] = v.args;
      const s = rustName(sql, 'a raw column');
      const k = rustKind(type);
      const e = exact ? 'true' : 'false';
      const sarg = sargable ? 'true' : 'false';
      const g = guard ? 'true' : 'false';
      const pref = (prefilter !== undefined && prefilter !== null) ? rustStr(prefilter) : '""';
      return `bind_raw(${s}, ${k}, ${e}, ${sarg}, ${g}, ${pref})`;
    }
    case 'columns':
      return `bind_columns(vec![${v.args.map(rustBinding).join(', ')}])`;
    case 'relation':
    case 'relationQuery': {
      const [from, alias, fields, scalar, corr, prefilter] = v.args;
      const fn = v.__call === 'relation' ? 'bind_relation' : 'bind_relation_query';
      if (fields === null || fields === undefined || Array.isArray(fields)
          || typeof fields !== 'object' || fields.__call || fields.__raw) {
        throw new Unrepresentable(`relation fields that are ${shapeOf(fields)}`);
      }
      const f = Object.entries(fields)
        .map(([k, b]) => `FieldEntry { name: ${rustStr(k)}.to_string(), binding: ${rustBinding(b)} }`).join(', ');
      const pref = (prefilter !== undefined && prefilter !== null) ? rustStr(prefilter) : '""';
      const sc = rustStr(scalar ?? '');
      const cr = rustName(corr ?? '', 'a correlation');
      const al = rustName(alias ?? '', 'an alias');
      const fr = rustName(from, 'a table name');
      return `${fn}(${fr}, ${al}, vec![${f}], ${sc}, ${cr}, ${pref})`;
    }
    case 'value': {
      const [val, type] = v.args;
      const t = (type === null || type === undefined) ? 'None' : `Some(${rustKind(type)})`;
      return `bind_value(${rustBinding(val)}, ${t})`;
    }
  }
  throw new Unrepresentable(`the binding constructor ${v.__call}`);
}

function rustRegister(ops) {
  return ops.map((op) => {
    if (op === null || typeof op !== 'object') {
      throw new Unrepresentable(`a register op that is ${shapeOf(op)}`);
    }
    if ('define' in op) {
      const a = op.define;
      if (!Array.isArray(a) || a.length !== 4) {
        throw new Unrepresentable(`a define that is ${shapeOf(a)}`);
      }
      const [dialect, section, key, entry] = a;
      if (!['ops', 'funcs', 'skel'].includes(section)) {
        throw new Unrepresentable(`the map section ${JSON.stringify(section)}`);
      }
      return `    sel_lang::sql::define(${rustStr(dialect)}, ${rustStr(section)}, ${rustStr(key)}, &serde_json::from_str::<serde_json::Value>(${JSON.stringify(JSON.stringify(entry))}).unwrap());`;
    }
    if ('dialect' in op) {
      const cleanOp = Object.fromEntries(Object.entries(op).filter(([k]) => k !== 'dialect'));
      return `    sel_lang::sql::define_dialect(${rustStr(op.dialect)}, &serde_json::from_str::<serde_json::Value>(${JSON.stringify(JSON.stringify(cleanOp))}).unwrap());`;
    }
    if ('function' in op) {
      const f = op.function;
      if (!Array.isArray(f) || f.length !== 3 || typeof f[0] !== 'string'
          || !Number.isInteger(f[1]) || !Number.isInteger(f[2])) {
        throw new Unrepresentable(`a function op that is ${shapeOf(f)}`);
      }
      return `    let _ = sel_lang::register_function(${rustStr(f[0])}, ${f[1]}, ${f[2]}, |_args| Ok(sel_lang::Value::text_owned(String::new())));`;
    }
    throw new Unrepresentable('a register op with neither define, dialect nor function');
  }).join('\n');
}

function emitRust(cases) {
  const bodies = [];
  const rows = [];

  cases.forEach((c, i) => {
    let unrep = null;
    let binds = '';
    let reg = '';
    try {
      binds = Object.entries(c.bindingCalls)
        .map(([n, x]) => `        m.insert(${rustStr(n)}.to_string(), ${rustBinding(x)});`).join('\n');
    } catch (e) {
      if (!(e instanceof Unrepresentable)) throw e;
      unrep = e.why;
    }
    if (unrep === null && c.registerData !== null && c.registerData !== undefined) {
      try {
        reg = rustRegister(c.registerData);
      } catch (e) {
        if (!(e instanceof Unrepresentable)) throw e;
        unrep = e.why;
      }
    }

    if (unrep !== null && !c.error && !c.throws) {
      throw new Error(
        `${c.at}: case ${c.name} cannot be written with the Rust constructors `
        + `(${unrep}) and does not assert a refusal, so Rust would lose the coverage `
        + 'rather than move it to compile time.');
    }

    const fn = `c${i}`;
    if (unrep === null) {
      bodies.push(`fn ${fn}_bind() -> HashMap<String, Binding> {\n    let mut m = HashMap::new();\n${binds}\n    m\n}`);
      if (reg) bodies.push(`fn ${fn}_reg() {\n${reg}\n}`);
    }

    const tablesStr = c.tableList === null ? '&[]' : `&[${(c.tableList ?? []).map((s) => rustStr(s)).join(', ')}]`;

    const f = [
      `name: ${rustStr(c.name)}`,
      `at: ${rustStr(c.at)}`,
      `dialect: ${rustStr(c.dialect ?? '')}`,
      `source: ${rustStr(c.source ?? '')}`,
      `expect: ${rustOptStr(c.expect)}`,
      `error: ${rustOptStr(c.error)}`,
      `throws: ${rustOptStr(c.throws)}`,
      `params: ${rustOptStr(c.params)}`,
      `as_mode: ${rustOptStr(c.as)}`,
      `mode: ${rustOptStr(c.mode)}`,
      `strict: ${c.optionsData && c.optionsData.strict ? 'true' : 'false'}`,
      `plan: ${rustOptStr(c.plan)}`,
      `has_tables: ${c.tableList !== null ? 'true' : 'false'}`,
      `tables: ${tablesStr}`,
      `unrepresentable: ${unrep === null ? 'None' : `Some(${rustStr(unrep)})`}`,
      `register_fn: ${unrep === null && reg ? `Some(${fn}_reg)` : 'None'}`,
      `bindings_fn: ${unrep === null ? `Some(${fn}_bind)` : 'None'}`,
    ];
    rows.push(`    SqlCase {\n        ${f.join(',\n        ')},\n    },`);
  });

  return `// ${BANNER.join('\n// ')}\n\n`
    + `use std::collections::HashMap;\n`
    + `use sel_lang::{Pos, Value};\n`
    + `use sel_lang::sql::{\n`
    + `    Binding, FieldEntry, SqlKind,\n`
    + `};\n\n`
    + `#[derive(Clone)]\n`
    + `pub struct TreeItem {\n    pub key: Option<String>,\n    pub val: Value,\n}\n\n`
    + `pub fn bin_from_hex(hex_str: &str) -> Value {\n`
    + `    let mut bytes = Vec::with_capacity(hex_str.len() / 2);\n`
    + `    for i in (0..hex_str.len()).step_by(2) {\n`
    + `        bytes.push(u8::from_str_radix(&hex_str[i..i + 2], 16).unwrap_or(0));\n`
    + `    }\n`
    + `    Value::bin(&bytes)\n`
    + `}\n\n`
    + `pub fn value_tree(items: Vec<TreeItem>) -> Value {\n`
    + `    let v = Value::list(vec![]);\n`
    + `    let mut i = 0;\n`
    + `    for it in items {\n`
    + `        let k = match it.key {\n`
    + `            Some(k) => k,\n`
    + `            None => {\n`
    + `                i += 1;\n`
    + `                i.to_string()\n`
    + `            }\n`
    + `        };\n`
    + `        let _ = v.set(&k, it.val, Pos::default());\n`
    + `    }\n`
    + `    v\n`
    + `}\n\n`
    + `fn bind_col(col: &str, table: &str, typ: SqlKind, exact: bool, sargable: bool, guard: bool, prefilter: &str) -> Binding {\n`
    + `    Binding::column(col, table, typ, exact, sargable, guard, "", prefilter, false)\n`
    + `}\n\n`
    + `fn bind_raw(raw: &str, typ: SqlKind, exact: bool, sargable: bool, guard: bool, prefilter: &str) -> Binding {\n`
    + `    Binding::raw(raw, typ, exact, sargable, guard, "", prefilter, false)\n`
    + `}\n\n`
    + `fn bind_columns(items: Vec<Binding>) -> Binding {\n`
    + `    Binding::columns(items)\n`
    + `}\n\n`
    + `fn bind_relation(from: &str, alias: &str, fields: Vec<FieldEntry>, scalar: &str, correlate: &str, prefilter: &str) -> Binding {\n`
    + `    Binding::relation(from, alias, fields, scalar, correlate, prefilter, false)\n`
    + `}\n\n`
    + `fn bind_relation_query(query: &str, alias: &str, fields: Vec<FieldEntry>, scalar: &str, correlate: &str, prefilter: &str) -> Binding {\n`
    + `    Binding::relation_query(query, alias, fields, scalar, correlate, prefilter, false)\n`
    + `}\n\n`
    + `fn bind_value(val: Value, typ: Option<SqlKind>) -> Binding {\n`
    + `    Binding::value(val, typ)\n`
    + `}\n\n`
    + `${bodies.join('\n\n')}\n\n`
    + `pub struct SqlCase {\n`
    + `    pub name: &'static str,\n`
    + `    pub at: &'static str,\n`
    + `    pub dialect: &'static str,\n`
    + `    pub source: &'static str,\n`
    + `    pub expect: Option<&'static str>,\n`
    + `    pub error: Option<&'static str>,\n`
    + `    pub throws: Option<&'static str>,\n`
    + `    pub params: Option<&'static str>,\n`
    + `    pub as_mode: Option<&'static str>,\n`
    + `    pub mode: Option<&'static str>,\n`
    + `    pub strict: bool,\n`
    + `    pub plan: Option<&'static str>,\n`
    + `    pub has_tables: bool,\n`
    + `    pub tables: &'static [&'static str],\n`
    + `    pub unrepresentable: Option<&'static str>,\n`
    + `    pub register_fn: Option<fn()>,\n`
    + `    pub bindings_fn: Option<fn() -> HashMap<String, Binding>>,\n`
    + `}\n\n`
    + `pub const SQL_CASES: &[SqlCase] = &[\n${rows.join('\n')}\n];\n`;
}

const OUTPUTS = [
  ['php/bin/CaseData.php', emitPhp],
  ['python/bin/case_data.py', emitPython],
  ['js/bin/case-data.mjs', emitJs],
  ['cpp/bin/case_data.cpp', emitCpp],
  ['lisp/bin/case-data.lisp', emitLispCases],
  ['go/bin/sqlt/case_data_gen.go', emitGo],
  ['rust/dev/src/bin/sqlt/case_data.rs', emitRust],
];
writeOrCheck('gen-sql-cases', ROOT, OUTPUTS.map(([rel, emit]) => [rel, emit(cases)]),
  { check, rerun: 'node tools/gen-sql-cases.mjs' });
if (check) process.stdout.write(`sql cases are current — ${cases.length} case(s)\n`);
