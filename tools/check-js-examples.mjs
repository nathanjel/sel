#!/usr/bin/env node
// The JS examples that tools/check-examples.sh cannot run.
//
// 1. Reference fragments. An examples/<cat>/ marked REFERENCE holds a function
//    as a fragment of js/src/builtins/*.mjs, not a program, so no lane ran it
//    and a broken one (fn-complex once tested `list.size > 0`, a method compared
//    with 0) shipped quoted in docs/contributing.md. Here each fragment's
//    EXAMPLE region is injected as a module whose relative imports resolve where
//    the fragment says it lives, it registers through the same define() the
//    shipped builtins use, and the category's cases.selt is run against it with
//    the conformance runner's own reader and checker.
// 2. The top-level host examples (examples/host-js.mjs, integration-js.mjs):
//    each must exit 0. They ship in the npm package and nothing else runs them.
//
//     node tools/check-js-examples.mjs

import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

// The fragments import js/src internals, so the runner must evaluate with the
// same module instances: never a bundle.
delete process.env.SEL_JS_ENTRY;

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'js/src');
const BUILTINS = join(SRC, 'builtins');
const { parseSelt, runCase, check } = await import('../js/bin/conformance.mjs');

let failures = 0;
let ran = 0;
const fail = (what) => { failures += 1; console.log(`FAIL ${what}`); };

// The region between the EXAMPLE markers, with each relative import pointed at
// the file it names from js/src/builtins/, and the two names a fragment may
// use without importing (fn-simple is written as if inside a builtin module
// that already has them) imported when it does not.
function fragmentModule(file) {
  const text = readFileSync(file, 'utf8');
  const m = /\/\/ EXAMPLE-BEGIN[^\n]*\n([\s\S]*?)\n[ \t]*\/\/ EXAMPLE-END/.exec(text);
  if (!m) throw new Error(`${file}: no EXAMPLE-BEGIN/EXAMPLE-END region`);
  let body = m[1].replace(/from\s+(['"])(\.{1,2}\/[^'"]+)\1/g,
    (_, q, spec) => `from ${JSON.stringify(pathToFileURL(resolve(BUILTINS, spec)).href)}`);
  const imported = (name) => new RegExp(`import\\s*\\{[^}]*\\b${name}\\b[^}]*\\}`).test(body);
  const preamble = [];
  if (!imported('define')) preamble.push(`import { define } from ${JSON.stringify(pathToFileURL(join(SRC, 'registry.mjs')).href)};`);
  if (!imported('Value')) preamble.push(`import { Value } from ${JSON.stringify(pathToFileURL(join(SRC, 'value.mjs')).href)};`);
  body = `${preamble.join('\n')}\n${body}\n`;
  return `data:text/javascript,${encodeURIComponent(body)}`;
}

const EXAMPLES = join(ROOT, 'examples');
for (const cat of readdirSync(EXAMPLES).sort()) {
  const dir = join(EXAMPLES, cat);
  if (!existsSync(join(dir, 'REFERENCE')) || !existsSync(join(dir, 'js.mjs'))) continue;
  const casesFile = join(dir, 'cases.selt');
  if (!existsSync(casesFile)) { fail(`${cat}: a JS fragment with no cases.selt`); continue; }
  try {
    await import(fragmentModule(join(dir, 'js.mjs')));
  } catch (e) {
    fail(`${cat}/js.mjs does not load: ${e.message}`);
    continue;
  }
  const cases = parseSelt(readFileSync(casesFile, 'utf8'), `examples/${cat}/cases.selt`);
  if (cases.length === 0) fail(`${cat}/cases.selt holds no cases`);
  for (const c of cases) {
    ran += 1;
    const r = runCase(c);
    const problem = r.suiteError ? `setup failed: ${r.suiteError}` : check(c.expect, r.value, r.error, c.at);
    if (problem !== null) fail(`${cat} ${c.name}: ${problem}`);
  }
}

for (const script of ['host-js.mjs', 'integration-js.mjs']) {
  ran += 1;
  const r = spawnSync(process.execPath, [join(EXAMPLES, script)], { encoding: 'utf8' });
  if (r.status !== 0) {
    fail(`examples/${script} exited ${r.status ?? r.signal}: ${(r.stderr || '').trim().split('\n').slice(-3).join(' | ')}`);
  }
}

if (ran === 0) fail('nothing was checked');
console.log(`check-js-examples: ${ran - failures} of ${ran} passed`);
process.exit(failures ? 1 : 0);
