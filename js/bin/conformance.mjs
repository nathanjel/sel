#!/usr/bin/env node
// Conformance runner. The suite in conformance/ is normative; this program has
// no opinions of its own beyond the file format in conformance/README.md.
//
// Note that expectation strings are unescaped by this file's own tiny escape
// reader, not by SEL's lexer — the suite must not validate the lexer with the
// lexer.

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
// SEL_JS_ENTRY aims this runner at a different build of the implementation —
// tools/impls.sh sets it to dist/sel.mjs so the bundle is held to the same
// suite as the source. Dynamic import because the specifier is not a constant.
const { compile, Value, SelError } =
  await import(process.env.SEL_JS_ENTRY ?? '../src/sel.mjs');

const HERE = dirname(fileURLToPath(import.meta.url));
const SUITE = resolve(HERE, '../../conformance');

// --- .selt parsing ----------------------------------------------------------

export function parseSelt(text, file) {
  const cases = [];
  let cur = null;
  let section = null;
  const lines = text.split('\n');

  lines.forEach((line, idx) => {
    const at = `${file}:${idx + 1}`;
    if (line.startsWith('### ')) {
      const m = /^###\s+name:\s*(\S+)\s*$/.exec(line);
      if (!m) throw new Error(`${at}: malformed case header`);
      cur = { name: m[1], at, setup: null, source: null, expect: null };
      cases.push(cur);
      section = null;
      return;
    }
    if (line === '===') { cur = null; section = null; return; }
    if (line.startsWith('--- ')) {
      if (!cur) throw new Error(`${at}: section outside a case`);
      section = line.slice(4).trim();
      if (['setup', 'source', 'expect'].includes(section)) cur[section] = [];
      else if (section !== 'note') throw new Error(`${at}: unknown section ${section}`);
      return;
    }
    if (!cur || !section) return;                       // header text, ignored
    if (section === 'note') return;
    cur[section].push(line);
  });

  return cases.map((c) => {
    if (c.source === null) throw new Error(`${c.at}: case ${c.name} has no --- source`);
    if (c.expect === null) throw new Error(`${c.at}: case ${c.name} has no --- expect`);
    return {
      ...c,
      setup: c.setup === null ? null : c.setup.join('\n').trim(),
      source: c.source.join('\n').trim(),
      expect: c.expect.join('\n').trim(),
    };
  });
}

// --- expectations -----------------------------------------------------------

function unescape(lit, at) {
  if (lit.length < 2 || lit[0] !== '"' || lit[lit.length - 1] !== '"') {
    throw new Error(`${at}: expected a quoted string, got ${lit}`);
  }
  const body = lit.slice(1, -1);
  let out = '';
  for (let i = 0; i < body.length; i++) {
    if (body[i] !== '\\') { out += body[i]; continue; }
    const e = body[++i];
    if (e === '\\') out += '\\';
    else if (e === '"') out += '"';
    else if (e === 'n') out += '\n';
    else if (e === 't') out += '\t';
    else if (e === 'r') out += '\r';
    else if (e === 'u') { out += String.fromCharCode(parseInt(body.slice(i + 1, i + 5), 16)); i += 4; }
    else throw new Error(`${at}: bad escape \\${e}`);
  }
  return out;
}

// Describes an actual result in the same vocabulary the expectations use, so a
// failure report reads as "want X, got Y" in one language.
function describe(value) {
  switch (value.kind) {
    case 'TEXT': return value.size ? `tree ${value.dump()}` : `text ${JSON.stringify(value.scalar)}`;
    case 'BIN': return value.size ? `tree ${value.dump()}` : `bin ${value.dump().slice(1)}`;
    case 'BOOL': return value.size ? `tree ${value.dump()}` : `bool ${value.scalar ? 'TRUE' : 'FALSE'}`;
    default: return value.size ? `tree ${value.dump()}` : 'none';
  }
}

function check(expect, value, error, at) {
  const space = expect.indexOf(' ');
  const form = space < 0 ? expect : expect.slice(0, space);
  const rest = space < 0 ? '' : expect.slice(space + 1).trim();

  if (form === 'error') {
    if (!error) return `expected ${expect}, got value ${describe(value)}`;
    const m = /^(\S+)(?:\s+at\s+(\d+):(\d+))?$/.exec(rest);
    if (!m) throw new Error(`${at}: malformed error expectation`);
    if (error.code !== m[1]) return `expected ${m[1]}, got ${error.code} (${error.message})`;
    if (m[2] !== undefined) {
      const gotAt = `${error.line}:${error.col}`;
      const wantAt = `${m[2]}:${m[3]}`;
      if (gotAt !== wantAt) return `expected ${m[1]} at ${wantAt}, got it at ${gotAt}`;
    }
    return null;
  }

  if (error) return `expected ${expect}, got ${error.code} (${error.message})`;

  switch (form) {
    case 'text':
      if (value.kind !== 'TEXT' || value.size) return `wanted text, got ${describe(value)}`;
      return value.scalar === unescape(rest, at) ? null : `got ${describe(value)}`;
    case 'num':
      if (value.kind !== 'TEXT' || value.size) return `wanted a number, got ${describe(value)}`;
      return value.scalar === rest ? null : `got ${describe(value)}`;
    case 'bin':
      if (value.kind !== 'BIN' || value.size) return `wanted binary, got ${describe(value)}`;
      return value.dump().slice(1) === rest ? null : `got ${describe(value)}`;
    case 'bool':
      if (value.kind !== 'BOOL' || value.size) return `wanted a boolean, got ${describe(value)}`;
      return (value.scalar ? 'TRUE' : 'FALSE') === rest ? null : `got ${describe(value)}`;
    case 'none':
      return (value.kind === 'NONE' && value.size === 0) ? null : `got ${describe(value)}`;
    case 'tree':
      return value.dump() === rest ? null : `got tree ${value.dump()}`;
    default:
      throw new Error(`${at}: unknown expectation form ${form}`);
  }
}

// --- running ----------------------------------------------------------------

function runCase(c) {
  const root = Value.none();
  if (c.setup) {
    try {
      compile(c.setup).run(root);
    } catch (e) {
      // A broken setup is a suite bug, not a failing implementation.
      return { suiteError: e instanceof SelError ? e.toString() : String(e) };
    }
  }
  try {
    return { value: compile(c.source).run(root) };
  } catch (e) {
    if (e instanceof SelError) return { error: e };
    throw e;
  }
}

function main() {
  const args = process.argv.slice(2);
  const files = args.length
    ? args.map((f) => resolve(f))
    : readdirSync(SUITE).filter((f) => f.endsWith('.selt')).sort().map((f) => join(SUITE, f));

  let pass = 0;
  const failures = [];
  const suiteErrors = [];
  const seen = new Map();

  for (const file of files) {
    const short = file.startsWith(SUITE) ? file.slice(SUITE.length + 1) : file;
    for (const c of parseSelt(readFileSync(file, 'utf8'), short)) {
      if (seen.has(c.name)) {
        suiteErrors.push(`${c.at}: duplicate case name ${c.name} (also ${seen.get(c.name)})`);
        continue;
      }
      seen.set(c.name, c.at);

      const r = runCase(c);
      if (r.suiteError) {
        suiteErrors.push(`${c.at}: ${c.name}: setup failed: ${r.suiteError}`);
        continue;
      }
      const problem = check(c.expect, r.value, r.error, c.at);
      if (problem === null) pass++;
      else failures.push({ ...c, problem });
    }
  }

  for (const f of failures) {
    console.log(`FAIL ${f.name}  (${f.at})`);
    console.log(`     source: ${f.source.replace(/\n/g, '\n             ')}`);
    console.log(`     want:   ${f.expect}`);
    console.log(`     ${f.problem}`);
  }
  for (const e of suiteErrors) console.log(`SUITE ${e}`);

  console.log(`\n${pass} passed, ${failures.length} failed, ${suiteErrors.length} suite errors`);
  process.exit(failures.length || suiteErrors.length ? 1 : 0);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) main();
