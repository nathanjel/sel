#!/usr/bin/env node
// The builtin manifest against the hosts' BEHAVIOUR, not their tables: what the
// startup reconciliation cannot see. Two probes, both predicted from
// spec/builtins.json alone and observed on every available host:
//
//   accepted counts  every builtin called with every count around its range
//                    (and every parity / allowed edge) — compiles, or E_ARITY
//                    at the call. Observed through the batch runner.
//   binding forms    every form of every binding builtin, with a distinct
//                    variable in each argument, plus a variant that defeats
//                    each guard — which names dependencies() reports is which
//                    argument it read, and where. Observed through --deps.
//
// The predictions are the manifest's, the observations the hosts'; nothing a
// host produces feeds back into what it is checked against.
//
//     node tools/check-manifest.mjs

import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
// SEL_BUILTIN_MANIFEST points the check at another manifest file: how the
// check is itself checked (a doctored copy must produce disagreements).
const MANIFEST = JSON.parse(readFileSync(process.env.SEL_BUILTIN_MANIFEST ?? resolve(ROOT, 'spec/builtins.json'), 'utf8')).builtins;
const IMPLS = execFileSync('bash', ['-c', '. tools/impls.sh; available_impls'], { cwd: ROOT, encoding: 'utf8' }).trim().split(/\s+/).filter(Boolean);
const work = mkdtempSync(join(tmpdir(), 'sel-manifest-'));
let failures = 0;
const complain = (msg) => { failures++; console.error(`manifest: ${msg}`); };

// --- accepted counts ---------------------------------------------------------

function accepts(a, count) {
  if (count < a.min) return false;
  if (a.max !== 'variadic' && count > a.max) return false;
  if (a.allowed && !a.allowed.includes(count)) return false;
  if (a.parity && (count % 2 === 1) !== (a.parity === 'odd')) return false;
  return true;
}
const arityProbes = [];
for (const [name, e] of Object.entries(MANIFEST)) {
  const a = e.arity;
  const hi = a.max === 'variadic' ? a.min + 3 : a.max + 2;
  for (let c = Math.max(0, a.min - 2); c <= hi; c++) {
    arityProbes.push({ name, count: c, at: '1:1', accepted: accepts(a, c),
      source: `${name}(${Array(c).fill('1').join(', ')})` });
    // The same count through the `.>` form, the left operand supplying the
    // first argument. A second parse path with its own copy of the arity rule
    // was pinned by two hand-written cases until SEL-0038 gave both paths one
    // body; this keeps it pinned per builtin either way. The name token sits
    // at column 6.
    if (c >= 1) {
      arityProbes.push({ name, count: c, at: '1:6', accepted: accepts(a, c),
        source: `1 .> ${name}(${Array(c - 1).fill('1').join(', ')})` });
    }
  }
}
const corpus = arityProbes.map((p) => `### ${p.name}/${p.count}${p.at === '1:6' ? '/pipe' : ''}\n${p.source}\n`).join('');
writeFileSync(join(work, 'arity.selc'), corpus);
for (const impl of IMPLS) {
  let out;
  try {
    out = execFileSync('bash', ['-c', `. tools/impls.sh; impl_batch ${impl} "$1"`, 'x', join(work, 'arity.selc')], { cwd: ROOT, encoding: 'utf8', maxBuffer: 1 << 26 });
  } catch (e) {
    complain(`${impl}: the batch runner failed: ${String(e.message).split('\n')[0]}`);
    continue;
  }
  const lines = out.split('\n');
  let bad = 0;
  arityProbes.forEach((p, i) => {
    const observed = lines[i] ?? '';
    const isArity = observed.startsWith('!E_ARITY@');
    if (p.accepted && isArity) { bad++; complain(`${impl}: ${p.source} — the manifest accepts ${p.count} arguments, the host says ${observed}`); }
    if (!p.accepted && observed !== `!E_ARITY@${p.at}`) { bad++; complain(`${impl}: ${p.source} — the manifest refuses ${p.count} arguments, the host answered ${observed || '(nothing)'}`); }
  });
  if (!bad) console.log(`manifest: ${impl} accepts exactly the manifest's counts (${arityProbes.length} probes)`);
}

// --- binding forms -------------------------------------------------------------

// The manifest's own classification (spec/builtins.md), on argument
// descriptors: { kind: 'name' | 'grouped' | 'text' | 'var', name }. A plain
// variable IS a bare name syntactically — the parser cannot tell `K` meant as a
// key from `K` meant as a binder — so it satisfies a bare-name guard; only a
// grouped `(K)` or a literal does not.
function classify(forms, args) {
  for (const f of forms) {
    if (f.roles.length !== args.length) continue;
    if (f.when) {
      const a = args[f.when.arg];
      const bare = a.kind === 'name' || a.kind === 'var';
      if (f.when.is === 'name' ? !bare : a.kind !== 'text') continue;
    }
    return f;
  }
  return null;
}
function predictDeps(form, args) {
  if (!form) return args.filter((a) => a.kind !== 'text').map((a) => a.name);
  const binds = new Set(form.binds);
  form.roles.forEach((r, i) => { if (r === 'binder' && (args[i].kind === 'name' || args[i].kind === 'var')) binds.add(args[i].name); });
  const deps = new Set();
  form.roles.forEach((r, i) => {
    const a = args[i];
    if (r === 'binder' || a.kind === 'text') return;
    if (!binds.has(a.name)) deps.add(a.name);
  });
  return [...deps].sort();
}
const render = (a) => (a.kind === 'text' ? '"T"' : a.kind === 'grouped' ? `(${a.name})` : a.name);
const formProbes = [];
for (const [name, e] of Object.entries(MANIFEST)) {
  if (!e.forms) continue;
  e.forms.forEach((f, fi) => {
    // Each argument its own name; a binder slot a bare name. Where an EARLIER
    // form for this count has a bare-name guard, a plain variable in that slot
    // would be taken for the binder, so this form is reached with a grouped
    // name there -- exactly how a rule author reaches it.
    const earlierNameGuards = e.forms.slice(0, fi).filter((g) => g.roles.length === f.roles.length && g.when && g.when.is === 'name').map((g) => g.when.arg);
    const args = f.roles.map((r, i) => (r === 'binder' ? { kind: 'name', name: `B${i}` }
      : earlierNameGuards.includes(i) ? { kind: 'grouped', name: `V${i}` } : { kind: 'var', name: `V${i}` }));
    if (f.when && f.when.is === 'text') args[f.when.arg] = { kind: 'text', name: null };
    formProbes.push({ name, label: `${name} form ${fi + 1}`, args });
    if (f.when) {   // defeat the guard: a later form for this count must apply
      const alt = args.map((a) => ({ ...a }));
      alt[f.when.arg] = f.when.is === 'name' ? { kind: 'grouped', name: `B${f.when.arg}` } : { kind: 'var', name: `V${f.when.arg}` };
      formProbes.push({ name, label: `${name} form ${fi + 1}, guard defeated`, args: alt });
    }
  });
}
for (const p of formProbes) {
  p.source = `${p.name}(${p.args.map(render).join(', ')})`;
  p.expected = predictDeps(classify(MANIFEST[p.name].forms, p.args), p.args);
  // Grouped binders and variables read where a name was expected are the
  // evaluator's E_EXPECT_SYMBOL when run; here the tree is only walked.
}
for (const impl of IMPLS) {
  let bad = 0;
  for (const p of formProbes) {
    const file = join(work, 'form.sel');
    writeFileSync(file, p.source);
    let out;
    try {
      out = execFileSync('bash', ['-c', `. tools/impls.sh; impl_deps ${impl} "$1" 2>&1`, 'x', file], { cwd: ROOT, encoding: 'utf8' });
    } catch (e) { out = String(e.stdout || e.message); }
    const lines = out.split('\n').map((l) => l.trim()).filter(Boolean);
    if (lines.some((l) => /^E_[A-Z0-9_]+ at /.test(l))) { bad++; complain(`${impl}: ${p.label} — ${p.source} did not compile: ${lines[0]}`); continue; }
    const observed = lines.sort();
    if (observed.join(' ') !== p.expected.join(' ')) {
      bad++;
      complain(`${impl}: ${p.label} — ${p.source}\n    manifest reads: ${p.expected.join(' ') || '(none)'}\n    host reads:     ${observed.join(' ') || '(none)'}`);
    }
  }
  if (!bad) console.log(`manifest: ${impl} classifies every binding form as the manifest does (${formProbes.length} probes)`);
}

rmSync(work, { recursive: true, force: true });
if (failures) { console.error(`manifest: ${failures} disagreement(s) with spec/builtins.json`); process.exit(1); }
console.log(`manifest: ${IMPLS.length} host(s) agree with spec/builtins.json on accepted counts and binding forms`);
