# A ray tracer in SEL

[`raytrace.sel`](raytrace.sel) draws the SEL mark, `•>`, in glass: the dot and
the chevron of [`docs/assets/logo-mark.svg`](../../docs/assets/logo-mark.svg)
standing on the mark's purple-to-teal tile, on a checkered floor, in a dark
studio lit by three softboxes. Every host renders it, and every host renders
the same bytes.

```sh
PYTHONPATH=python python3 examples/raytrace/python.py           # the transcript, output.txt
node examples/raytrace/js.mjs --ppm 1280 720 2 > mark.ppm        # any size; SS 2 = 4 rays a pixel
python3 tools/ppm-to-png.py mark.ppm mark.png
node examples/raytrace/js.mjs --bench report.json                # the benchmark frame
```

Every host's program takes the same three modes (`cpp/build/example-raytrace`,
`rust/build/example-raytrace`, `go/build/example-raytrace`, and the
`php.php`, `lisp.lisp` and `python.py` beside this file, run as
[`tools/impls.sh`](../../tools/impls.sh) `impl_example` runs them).

## The scene in a language without functions

SEL has no user functions, no recursion and no loops, so the tracer is written
the way the language allows:

- **Loops are `MAP`s over lists of the right length.** `RANGE_X` is
  `MAP(SPLIT(REPEAT(",", W - 1), ","), _K - 1)`, and a ray's path through the
  glass is a `MAP` over eight bounces that stops doing work once the ray is out.
- **Shading is written once.** A ray that leaves the glass, and the reflection
  every surface it enters gives off, is appended to `RAYS`; the floor, its
  shadows and the sky are computed for that list at the end.
- **Every piece of glass is a capsule.** A sphere is a capsule whose ends
  coincide, and the chevron is two capsules that are one solid: a surface that
  lies inside another piece of the same group is not a surface, which is what
  keeps the joint clean.
- **The optics are the textbook ones**: Snell's law with total internal
  reflection, Schlick's Fresnel term, absorption over the distance travelled in
  the glass (`1 / (1 + x + x²/2)`, which needs no `EXP`), shadows tinted by the
  glass the sunlight crossed, `SS × SS` samples a pixel, and gamma 2 — a
  square root — on the way out.
- **There are no floats.** Every number is an exact decimal, and the scene
  keeps six fractional digits between steps (`ROUND(…, P)`), so a pixel costs a
  few hundred operations on short numbers. `examples/mandelbrot.sel` never
  rounds, which is why its numbers grow to thousands of digits.

## `SQRT`, a host function

SEL has no square root on purpose ([SPEC §7.6](../../spec/SPEC.md#76-numbers)):
a square root has no exact decimal result. An application that wants one
registers it ([SPEC §8.1](../../spec/SPEC.md#81-host-functions)), and this one
defines it the way `/` is defined:

`SQRT(x [, n])` is the square root of `x` to `n` fractional digits, 10 if `n`
is left out. A root that has at most `n` fractional digits comes back exact, at
its minimal scale (`SQRT(2.25)` is `1.5`, `SQRT(1000000, 3)` is `1000`); any
other is rounded half away from zero to exactly `n` digits (`SQRT(2)` is
`1.4142135624`, `SQRT(0.0025, 1)` is `0.1`). `x` is read first and `n` second,
each with the builtins' own readers and errors; then an `n` above 1 000 000 (the
cap `ROUND`'s scale has) is `E_RANGE` at `n`, and a negative `x` is `E_RANGE`
at `x`.

It is computed on whole numbers, and no host uses floating point anywhere in
it, so every host gives every digit the same. With `x = m / 10^s`:

1. **An exact root comes from `x` itself.** Make the scale even — `m` and `s`,
   or `10m` and `s + 1` — and `x` has a terminating square root exactly when
   that `m` is a perfect square. Its root, with the zeros it does not need
   dropped, is the answer if it has at most `n` fractional digits. This step
   costs what `x`'s size costs: `SQRT(4, 1000000)` is `isqrt(4)`, not a
   two-million-digit root with a million zeros to drop.
2. **Any other root is rounded at scale `n`.**

   ```
   sqrt(x) × 10^n = sqrt(m × 10^(2n − s))
   ```

   Write the radicand as `v / p`: `p` is 1 unless `x` has more than `2n`
   fractional digits, and then a power of ten. The integer square root `r` of
   `v div p` is the truncated result, and one comparison of whole numbers,
   `4v ≥ (2r + 1)²·p`, decides whether to round up. This step never meets an
   exact root, since the first step would have returned it.

The integer square root is each host's own. Python has `math.isqrt`, Go has
`(*big.Int).Sqrt` and Lisp has `isqrt`. JS uses Newton's method on `BigInt`.
PHP uses native integers up to 18 digits, GMP beyond them when it is loaded,
and otherwise the schoolbook digit-pair method on digit strings. C++ and Rust
use a 128-bit fast path (Newton on integers) and otherwise the same digit-pair
method on base-10⁹ limbs. The digit-pair method is O(d²) in the root's digits:
a ten-thousand-digit root takes about 0.2 s in C++ and Rust, and the time
grows with the square of the digits, where GMP takes 0.4 s for a million
digits. The scene only ever takes roots of numbers a dozen or two digits long,
which every host's fast path covers. The transcript's square roots include
operands past 128 bits.

## As a benchmark

`--bench REPORT.json` renders the 64 × 36 frame `RAYTRACE_WARMUPS` (2) times
unmeasured and `RAYTRACE_RUNS` (5) times measured, timing only the program's
run, and records each frame's CRC32;
[`tools/commit-benchmark/snapshot.py`](../../tools/commit-benchmark/README.md)
runs it in every host as its `raytrace` workload and refuses a frame whose
CRC32 is not the one in [`output.txt`](output.txt).
