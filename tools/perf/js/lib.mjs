// Shared harness for the JS performance worklist (docs/interim/2026-09-29/worklist/
// performance/js.md). Fixed seeds, semantic checksums, warm-up, repeated runs with the
// median and spread reported. `SEL_JS_ENTRY` selects src (default), dist/sel.mjs or
// dist/sel.min.mjs like the other JS tools.
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
export const ENTRY = process.env.SEL_JS_ENTRY || resolve(ROOT, 'js/src/sel.mjs');
export const sel = await import(pathToFileURL(ENTRY).href);
export const root = ROOT;

// A tiny deterministic generator (xorshift32): same input on every run.
export function rng(seed = 1) {
  let x = seed >>> 0 || 1;
  return () => { x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; return x / 4294967296; };
}

// Run fn `reps` times after `warm` warm-ups; return {median, min, max} in ms and the
// last result (for the checksum). The process' own CPU time is what matters on a loaded
// machine, so report both wall and CPU medians.
export function bench(label, fn, { reps = 7, warm = 2, quiet = false } = {}) {
  let last;
  for (let i = 0; i < warm; i++) last = fn();
  const wall = [], cpu = [];
  for (let i = 0; i < reps; i++) {
    const c0 = process.cpuUsage(), t0 = process.hrtime.bigint();
    last = fn();
    const t1 = process.hrtime.bigint(), c1 = process.cpuUsage(c0);
    wall.push(Number(t1 - t0) / 1e6);
    cpu.push((c1.user + c1.system) / 1e3);
  }
  const med = (a) => [...a].sort((x, y) => x - y)[a.length >> 1];
  const r = { label, wall: med(wall), cpu: med(cpu), min: Math.min(...wall), max: Math.max(...wall), last };
  if (!quiet) console.log(`${label.padEnd(44)} wall ${r.wall.toFixed(2).padStart(9)} ms  cpu ${r.cpu.toFixed(2).padStart(9)} ms  [${r.min.toFixed(1)}..${r.max.toFixed(1)}]  => ${String(last).slice(0, 40)}`);
  return r;
}

// Growth check: run `make(n)` for n, 2n, 4n and print the ratio of successive medians
// (about 2 for linear, about 4 for quadratic).
export function growth(label, make, ns, opts = {}) {
  const rs = ns.map((n) => bench(`${label} n=${n}`, make(n), { ...opts, quiet: true }));
  const out = rs.map((r, i) => `${ns[i]}: ${r.cpu.toFixed(1)} ms${i ? ` (x${(r.cpu / rs[i - 1].cpu).toFixed(2)})` : ''}`);
  console.log(`${label.padEnd(44)} ${out.join('   ')}`);
  return rs;
}

export function run(src, ctx = {}) {
  return sel.compile(src).run(ctx);
}
