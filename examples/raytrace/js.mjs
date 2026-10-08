// A ray tracer in SEL, and the host function it needs -- from JavaScript.
//
//   node examples/raytrace/js.mjs
//   node examples/raytrace/js.mjs --ppm 640 360 2 > mark.ppm
//   node examples/raytrace/js.mjs --bench report.json
//
// raytrace.sel draws the SEL mark in glass. SEL has no square root -- a square
// root has no exact decimal result -- so the application gives it one: SQRT(x, n)
// is the square root of x to n fractional digits (10 if n is left out). Like
// `/`, it is exact when it can be: a root with at most n fractional digits comes
// back at its minimal scale, and any other is rounded half away from zero to
// exactly n. It is computed on whole numbers, so every host gives every digit
// the same. For x = m / 10^s, x has an exact root when m, with the scale made
// even, is a perfect square -- which costs what x's size costs, whatever n is.
// Any other root is rounded from
//
//     sqrt(x) * 10^n = sqrt(m * 10^(2n - s))
//
// whose integer square root is the truncated answer; one comparison of whole
// numbers decides the rounding.
//
// With no arguments it prints a few square roots and a small frame; --ppm prints
// a frame of any size as PPM, and --bench times the frame the benchmarks use.
// The files beside this one print byte-identical output.

import { readFileSync, writeFileSync } from 'node:fs';
import { compile, registerFunction, SelError, Value } from '../../js/src/sel.mjs';

const MAX_SCALE = 1000000;   // the cap ROUND's scale has (spec/limits.json)

// EXAMPLE-BEGIN sqrt
// Newton's method on BigInt: 2^ceil(bits/2) >= sqrt(v).
function isqrt(v) {
  if (v < 2n) return v;
  let bits = 0;
  let temp = v;
  while (temp >= 0x100000000n) {
    temp >>= 32n;
    bits += 32;
  }
  bits += (temp === 0n ? 0 : 32 - Math.clz32(Number(temp)));
  let x = 1n << BigInt((bits + 1) >> 1);
  for (;;) {
    const y = (x + v / x) >> 1n;
    if (y >= x) return x;
    x = y;
  }
}

function sqrt(args) {
  const x = args.dec(0);
  const n = args.count() > 1 ? args.nonNegInt(1) : 10;
  if (n > MAX_SCALE) throw new SelError('E_RANGE', 'SQRT: scale above 1000000', args.posOf(1));
  if (x.neg) throw new SelError('E_RANGE', 'SQRT of a negative number', args.posOf(0));
  // x = m / 10^s. An exact root is found from x itself, so it costs what x's
  // size costs, whatever n is: with the scale made even, x = m2 / 10^s2, and
  // sqrt(x) is a terminating decimal exactly when m2 is a perfect square.
  const m = x.digits, s = x.scale;
  const [m2, s2] = s % 2 === 0 ? [m, s] : [m * 10n, s + 1];
  let r = isqrt(m2);
  if (r * r === m2) {
    let scale = s2 / 2, step = 1;             // drop the zeros it does not need,
    while (step * 2 <= scale) step *= 2;      // in halving chunks
    for (; step > 0 && scale > 0; step >>= 1) {
      const chunk = 10n ** BigInt(step);
      if (step <= scale && r % chunk === 0n) { r /= chunk; scale -= step; }
    }
    if (scale <= n) return Value.num({ neg: false, digits: r, scale });
  }
  // Any other root is rounded at scale n: sqrt(x) * 10^n = sqrt(m * 10^e) with
  // e = 2n - s; when e is negative that is sqrt(m / 10^-e), whose integer part
  // is isqrt(m / 10^-e).
  const e = 2 * n - s;
  const v = e >= 0 ? m * 10n ** BigInt(e) : m;
  const p = e >= 0 ? 1n : 10n ** BigInt(-e);
  r = isqrt(v / p);
  if (4n * v >= (2n * r + 1n) ** 2n * p) r += 1n;   // at or past the half: away from zero
  return Value.num({ neg: false, digits: r, scale: n });
}

registerFunction('SQRT', 1, 2, sqrt);
// EXAMPLE-END sqrt

const read = (name) => readFileSync(new URL(name, import.meta.url), 'utf8');
const frame = (scene, w, h, ss) =>
  scene.run(Value.fromNative({ W: String(w), H: String(h), SS: String(ss) })).asText();

function main(argv) {
  const scene = compile(read('raytrace.sel'));
  if (argv[0] === '--ppm' && argv.length === 4) {
    process.stdout.write(frame(scene, argv[1], argv[2], argv[3]));
    return 0;
  }
  if (argv[0] === '--bench' && argv.length === 2) return bench(scene, argv[1]);
  if (argv.length > 0) {
    console.error('usage: js.mjs [--ppm W H SS | --bench REPORT.json]');
    return 2;
  }

  console.log('1. SQRT, the one function the ray tracer needs from the host');
  for (const src of ['SQRT(2)', 'SQRT(2, 40)', 'SQRT(2.25)', 'SQRT(1000000, 3)', 'SQRT(0.000)',
                     'SQRT(6.25, 3000)', 'SQRT(0.0025, 1)', 'SQRT(0.0225, 1)', 'SQRT(99.999999, 2)',
                     'SQRT(POWER(12345678901234567890, 2))', 'SQRT(POWER(10, 41) + 1, 3)',
                     'SQRT(-4)', 'SQRT("four")', 'SQRT(4, -1)', 'SQRT(4, 0.5)', 'SQRT(4, 1000001)']) {
    try {
      console.log(`   ${src.padEnd(38)} => ${compile(src).run(Value.none()).asText()}`);
    } catch (e) {
      if (!(e instanceof SelError)) throw e;
      console.log(`   ${src.padEnd(38)} => ${e.code} at ${e.line}:${e.col}`);
    }
  }

  console.log('2. the scene, 64 x 36, one ray per pixel');
  const img = frame(scene, 64, 36, 1);
  const crc = compile('CRC32(IMG)').run(Value.fromNative({ IMG: img })).asText();
  console.log(`   ${img.length} bytes of PPM, CRC32 ${crc}`);
  return 0;
}

// The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
// pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5).
function bench(scene, report) {
  const warmups = Number(process.env.RAYTRACE_WARMUPS ?? 2);
  const runs = Number(process.env.RAYTRACE_RUNS ?? 5);
  const crc = compile('CRC32(IMG)');
  const samples = [], outputs = [];
  for (let i = 0; i < warmups + runs; i++) {
    const context = Value.fromNative({ W: '64', H: '36', SS: '1' });
    const t = performance.now();
    const img = scene.run(context).asText();
    const elapsed = performance.now() - t;
    if (i >= warmups) {
      samples.push(elapsed);
      outputs.push(crc.run(Value.fromNative({ IMG: img })).asText());
    }
  }
  writeFileSync(report, JSON.stringify({ samples_ms: samples, outputs, warmups, runs }));
  return 0;
}

process.exitCode = main(process.argv.slice(2));
