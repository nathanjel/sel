#!/usr/bin/env node
// Plain AST versus optimised execution, over the whole conformance corpus.
// Every `.selt` source (with its setup) is run twice on fresh contexts:
//
//   plain      evalNode(program.ast)        the tree exactly as parsed
//   physical   program.run()                what a caller gets: optimiser + math plans
//
// and the two must agree on everything observable: the ordered value dump, the
// error code and position, and the FINAL CONTEXT dump (assignments and their
// order). The optimiser is only correct if it cannot be seen (spec/SPEC.md 6.4:
// "the evaluator is the depth authority"; 7.4 for SORT+TAKE), so this holds the
// optimised path to the plain reading whatever the conformance file expects; a
// case whose answer the spec leaves open is therefore still checked for
// transparency. Each program is also run twice on the same Program, to catch a
// plan or cache that survives a failure.
//
//   node tools/check-eval-equivalence.mjs [file.selt ...]
//
// Exit status is non-zero when any source differs; the differing sources are
// listed with both answers. A gate lane of tools/check.sh ("JS plain vs
// optimised"); SEL_EVAL_EQUIVALENCE_ALLOW=<file of case names> lists exemptions.

import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const { compile, Value } = await import(process.env.SEL_JS_ENTRY ?? '../js/src/sel.mjs');
const internals = await import('../js/src/eval.mjs');
const { Context, evalNode } = internals;

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
// The conformance runner's own reader, so this lane and the suite cannot read
// a case differently.
const { parseSelt } = await import('../js/bin/conformance.mjs');
const cases = (file) => parseSelt(readFileSync(file, 'utf8'), file);

function observe(mode, program, root) {
  const ctx = new Context(root);
  let answer;
  try {
    const v = mode === 'plain' ? evalNode(program.ast, ctx) : program.run(root);
    answer = v.dump();
  } catch (e) {
    answer = `!${e.code ?? e.name}@${e.line}:${e.col}`;
  }
  let ctxDump;
  try { ctxDump = root.dump(); } catch (e) { ctxDump = `!${e.code ?? e.name}`; }
  return `${answer} | ctx=${ctxDump} | depth=${mode === 'plain' ? ctx.depth : 0}`;
}

function run(c, mode) {
  const root = Value.fromNative({});
  if (c.setup) compile(c.setup).run(root);
  let p;
  try { p = compile(c.source); } catch (e) {
    // A compile error is the same outcome in both modes: nothing is evaluated.
    const at = `!${e.code ?? e.name}@${e.line}:${e.col} (compile)`;
    return { first: at, second: at };
  }
  const first = observe(mode, p, root);
  // A second run on a fresh context with the same Program: a plan, cache or
  // scratch frame left behind by the first (failed) run would show here.
  const root2 = Value.fromNative({});
  if (c.setup) compile(c.setup).run(root2);
  const second = observe(mode, p, root2);
  return { first, second };
}

const allow = new Set(process.env.SEL_EVAL_EQUIVALENCE_ALLOW
  ? readFileSync(process.env.SEL_EVAL_EQUIVALENCE_ALLOW, 'utf8').split('\n').filter(Boolean) : []);
const files = process.argv.length > 2 ? process.argv.slice(2)
  : [...readdirSync(join(ROOT, 'conformance')).filter((f) => f.endsWith('.selt')).sort().map((f) => join(ROOT, 'conformance', f))];

let total = 0;
const bad = [];
for (const f of files) {
  for (const c of cases(f)) {
    total += 1;
    let plain, phys;
    try { plain = run(c, 'plain'); phys = run(c, 'physical'); } catch (e) { bad.push([c.name, `host error ${e.message}`, '']); continue; }
    const strip = (s) => s.replace(/ \| depth=\d+$/, '');
    if (plain.first !== plain.second) bad.push([c.name, 'plain run is not repeatable', `${plain.first}\n   vs ${plain.second}`]);
    else if (strip(plain.first) !== strip(phys.first)) bad.push([c.name, 'physical differs from plain', `plain=${strip(plain.first).slice(0, 160)}\n   phys =${strip(phys.first).slice(0, 160)}`]);
    else if (phys.first !== phys.second) bad.push([c.name, 'physical run is not repeatable', `${phys.first}\n   vs ${phys.second}`]);
    if (/depth=[1-9]/.test(plain.first)) bad.push([c.name, 'plain evaluation left depth counted', plain.first]);
  }
}
const reported = bad.filter(([n]) => !allow.has(n));
for (const [n, why, detail] of reported) console.log(`DIFF ${n}: ${why}\n   ${detail}`);
console.log(`${total} sources, ${reported.length} differ${allow.size ? ` (${bad.length - reported.length} allowed)` : ''}`);
process.exit(reported.length ? 1 : 0);
