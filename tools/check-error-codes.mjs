#!/usr/bin/env node
// Every error code a host raises is catalogued, and every catalogued code is
// raised by every host. The catalogue is spec/limits.json for the language
// (checked against spec/errors.md by gen-limits) and sql/errors.md for the SQL
// layer. A code raised in one host only, or listed and raised nowhere, fails.
//
//     node tools/check-error-codes.mjs

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const HOSTS = {
  js: ['js/src', '.mjs'], python: ['python/sel', '.py'], php: ['php/src', '.php'],
  cpp: ['cpp', '.cpp', '.hpp'], lisp: ['lisp/src', '.lisp'],
};
// How a host spells a raise. Generated map data is skipped: it quotes codes as
// data, not as raises.
const RAISE = /(?:fail|refuse)\(\s*['"](E_[A-Z0-9_]+)['"]|\((?:fail|refuse) "(E_[A-Z0-9_]+)"|SelError\(\s*['"](E_[A-Z0-9_]+)['"]|SqlError\(\s*['"](E_[A-Z0-9_]+)['"]|make-sel-error "(E_[A-Z0-9_]+)"/g;
const SKIP = /_map\.|MapData|map-data|_limits\.|Limits\.php|sel_limits|limits\.lisp|case[-_]data|CaseData|map[-_]replay|MapReplay/;

function files(dir, exts) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) { if (!['build', 'third_party', 'tests', 'bin', 'test_package'].includes(entry)) out.push(...files(p, exts)); }
    else if (exts.some((e) => p.endsWith(e)) && !SKIP.test(p)) out.push(p);
  }
  return out;
}

const language = Object.keys(JSON.parse(readFileSync(resolve(ROOT, 'spec/limits.json'), 'utf8')).errors);
const sql = [...readFileSync(resolve(ROOT, 'sql/errors.md'), 'utf8').matchAll(/^\| `(E_SQL_[A-Z0-9_]+)` \|/gm)].map((m) => m[1]);
const catalogue = new Set([...language, ...sql]);

let status = 0;
for (const [host, [dir, ...exts]] of Object.entries(HOSTS)) {
  const raised = new Set();
  for (const f of files(resolve(ROOT, dir), exts)) {
    for (const m of readFileSync(f, 'utf8').matchAll(RAISE)) raised.add(m.slice(1).find(Boolean));
  }
  const unknown = [...raised].filter((c) => !catalogue.has(c)).sort();
  const missing = [...catalogue].filter((c) => !raised.has(c)).sort();
  if (unknown.length) { console.error(`error codes: ${host} raises uncatalogued ${unknown.join(', ')}`); status = 1; }
  if (missing.length) { console.error(`error codes: ${host} never raises catalogued ${missing.join(', ')}`); status = 1; }
  if (!unknown.length && !missing.length) console.log(`error codes: ${host} raises exactly the ${catalogue.size} catalogued codes`);
}
process.exit(status);
