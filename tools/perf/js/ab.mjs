// A/B runner for the JS performance worklist: alternates a baseline copy of js/src with
// the working tree, N rounds each, and reports the MIN wall and CPU time per benchmark
// line (the load from other processes only ever adds time, so the minimum is the least
// contaminated estimate).
//   node tools/perf/js/ab.mjs <bench.mjs> <baseline-entry.mjs> [rounds]
import { spawnSync } from 'node:child_process';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
const here = dirname(fileURLToPath(import.meta.url));
const [bench, base, rounds = '3'] = process.argv.slice(2);
const benchPath = resolve(here, bench);
const run = (entry) => {
  const env = { ...process.env };
  if (entry) env.SEL_JS_ENTRY = entry; else delete env.SEL_JS_ENTRY;
  const r = spawnSync('node', ['--expose-gc', benchPath], { env, encoding: 'utf8', maxBuffer: 1 << 26 });
  const out = new Map();
  for (const line of r.stdout.split('\n')) {
    const m = /^(.*?)\s+wall\s+([\d.]+) ms\s+cpu\s+([\d.]+) ms/.exec(line);
    if (m) out.set(m[1].trim(), { wall: Number(m[2]), cpu: Number(m[3]) });
  }
  return out;
};
const best = { base: new Map(), work: new Map() };
const keep = (into, res) => { for (const [k, v] of res) { const c = into.get(k); if (!c) into.set(k, { ...v }); else { c.wall = Math.min(c.wall, v.wall); c.cpu = Math.min(c.cpu, v.cpu); } } };
for (let i = 0; i < Number(rounds); i++) { keep(best.base, run(base)); keep(best.work, run(null)); }
for (const [k, b] of best.base) {
  const w = best.work.get(k);
  if (!w) continue;
  console.log(`${k.padEnd(62)} base ${b.wall.toFixed(1).padStart(8)} / ${b.cpu.toFixed(1).padStart(8)} ms   now ${w.wall.toFixed(1).padStart(8)} / ${w.cpu.toFixed(1).padStart(8)} ms   x${(b.wall / w.wall).toFixed(2)} wall x${(b.cpu / w.cpu).toFixed(2)} cpu`);
}
