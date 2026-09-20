// SEL_JS_ROOT selects an unchanged source snapshot. Run variants sequentially.
import fs from 'node:fs';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
const root = process.env.SEL_JS_ROOT || process.cwd();
const { compile, Value } = await import(pathToFileURL(`${root}/js/src/sel.mjs`));
const rows = Array.from({ length: 10000 }, (_, i) => ({ id: i, name: `row${i}`, amount: '2.50' }));
const cases = [
  ['record', 'RECORD("id", X, "name", Y, "amount", Z)', { X: 3, Y: 'row', Z: '2.50' }, 20000],
  ['projection', 'ROWS .> MAP(RECORD("id", _["id"], "name", _["name"], "amount", _["amount"]))', { ROWS: rows }, 5],
  ['dynamic-record', 'RECORD(K, X, "name", Y)', { K: 'id', X: 3, Y: 'row' }, 20000],
  ['mandelbrot', fs.readFileSync('examples/mandelbrot.sel', 'utf8'), null, 1],
];
const results = {};
for (const [name, source, input, repetitions] of cases) {
  const program = compile(source);
  const context = input === null ? null : Value.fromNative(input);
  const run = () => program.run(context ?? Value.none());
  for (let i = 0; i < 5; i++) run();
  const samples_ms = [];
  for (let sample = 0; sample < 9; sample++) {
    const start = performance.now();
    for (let i = 0; i < repetitions; i++) run();
    samples_ms.push((performance.now() - start) / repetitions);
  }
  results[name] = { repetitions, samples_ms,
    output_sha256: createHash('sha256').update(run().dump()).digest('hex') };
}
fs.writeFileSync(process.argv[2], JSON.stringify({ runtime: process.version, results }, null, 2) + '\n');
