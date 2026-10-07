#!/usr/bin/env python3
"""A ray tracer in SEL, and the host function it needs -- from Python.

    PYTHONPATH=python python3 examples/raytrace/python.py
    PYTHONPATH=python python3 examples/raytrace/python.py --ppm 640 360 2 > mark.ppm
    PYTHONPATH=python python3 examples/raytrace/python.py --bench report.json

raytrace.sel draws the SEL mark in glass. SEL has no square root -- a square
root has no exact decimal result -- so the application gives it one: SQRT(x, n)
is the square root of x to n fractional digits (10 if n is left out). Like
`/`, it is exact when it can be: a root with at most n fractional digits comes
back at its minimal scale, and any other is rounded half away from zero to
exactly n. It is computed on whole numbers, so every host gives every digit
the same. For x = m / 10^s, x has an exact root when m, with the scale made
even, is a perfect square -- which costs what x's size costs, whatever n is.
Any other root is rounded from

    sqrt(x) * 10^n = sqrt(m * 10^(2n - s))

whose integer square root is the truncated answer; one comparison of whole
numbers decides the rounding.

With no arguments it prints a few square roots and a small frame; --ppm prints
a frame of any size as PPM, and --bench times the frame the benchmarks use.
The files beside this one print byte-identical output.
"""

import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
try:
    import sel                                                  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(HERE, '..', '..', 'python'))

from sel import SelError, Value, compile, register_function     # noqa: E402
from sel.decimal import Dec                                     # noqa: E402

MAX_SCALE = 1000000          # the cap ROUND's scale has (spec/limits.json)


# EXAMPLE-BEGIN sqrt
def sqrt(args):
    x = args.dec(0)
    n = args.non_neg_int(1) if args.count() > 1 else 10
    if n > MAX_SCALE:
        raise SelError('E_RANGE', 'SQRT: scale above 1000000', args.pos_of(1))
    if x.neg:
        raise SelError('E_RANGE', 'SQRT of a negative number', args.pos_of(0))
    # x = m / 10^s. An exact root is found from x itself, so it costs what x's
    # size costs, whatever n is: with the scale made even, x = m2 / 10^s2, and
    # sqrt(x) is a terminating decimal exactly when m2 is a perfect square.
    m, s = x.digits, x.scale
    m2, s2 = (m, s) if s % 2 == 0 else (m * 10, s + 1)
    r = math.isqrt(m2)
    if r * r == m2:
        scale = s2 // 2                     # drop the zeros it does not need,
        step = 1                            # in halving chunks
        while step * 2 <= scale:
            step *= 2
        while step and scale:
            if step <= scale and r % 10 ** step == 0:
                r //= 10 ** step
                scale -= step
            step //= 2
        if scale <= n:
            return Value.num(Dec(False, r, scale))
    # Any other root is rounded at scale n: sqrt(x) * 10^n = sqrt(m * 10^e) with
    # e = 2n - s; when e is negative that is sqrt(m / 10^-e), whose integer part
    # is isqrt(m // 10^-e).
    e = 2 * n - s
    v, p = (m * 10 ** e, 1) if e >= 0 else (m, 10 ** -e)
    r = math.isqrt(v // p)
    if 4 * v >= (2 * r + 1) ** 2 * p:       # at or past the half: away from zero
        r += 1
    return Value.num(Dec(False, r, n))


register_function('SQRT', 1, 2, sqrt)
# EXAMPLE-END sqrt


def read(name):
    with open(os.path.join(HERE, name), encoding='utf-8') as fh:
        return fh.read()


def frame(scene, w, h, ss):
    return scene.run(Value.from_native({'W': str(w), 'H': str(h), 'SS': str(ss)})).as_text()


def main(argv):
    scene = compile(read('raytrace.sel'))
    if argv[:1] == ['--ppm'] and len(argv) == 4:
        sys.stdout.write(frame(scene, *argv[1:]))
        return 0
    if argv[:1] == ['--bench'] and len(argv) == 2:
        return bench(scene, argv[1])
    if argv:
        print('usage: python.py [--ppm W H SS | --bench REPORT.json]', file=sys.stderr)
        return 2

    print('1. SQRT, the one function the ray tracer needs from the host')
    for src in ['SQRT(2)', 'SQRT(2, 40)', 'SQRT(2.25)', 'SQRT(1000000, 3)', 'SQRT(0.000)',
                'SQRT(6.25, 3000)', 'SQRT(0.0025, 1)', 'SQRT(0.0225, 1)', 'SQRT(99.999999, 2)',
                'SQRT(POWER(12345678901234567890, 2))', 'SQRT(POWER(10, 41) + 1, 3)',
                'SQRT(-4)', 'SQRT("four")', 'SQRT(4, -1)', 'SQRT(4, 0.5)', 'SQRT(4, 1000001)']:
        try:
            print(f'   {src:<38} => {compile(src).run(Value.none()).as_text()}')
        except SelError as e:
            print(f'   {src:<38} => {e.code} at {e.line}:{e.col}')

    print('2. the scene, 64 x 36, one ray per pixel')
    img = frame(scene, 64, 36, 1)
    crc = compile('CRC32(IMG)').run(Value.from_native({'IMG': img})).as_text()
    print(f'   {len(img)} bytes of PPM, CRC32 {crc}')
    return 0


def bench(scene, report):
    """The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
    pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5)."""
    warmups = int(os.environ.get('RAYTRACE_WARMUPS', '2'))
    runs = int(os.environ.get('RAYTRACE_RUNS', '5'))
    crc = compile('CRC32(IMG)')
    samples, outputs = [], []
    for i in range(warmups + runs):
        context = Value.from_native({'W': '64', 'H': '36', 'SS': '1'})
        t = time.perf_counter()
        img = scene.run(context).as_text()
        elapsed = (time.perf_counter() - t) * 1000
        if i >= warmups:
            samples.append(elapsed)
            outputs.append(crc.run(Value.from_native({'IMG': img})).as_text())
    with open(report, 'w', encoding='utf-8') as fh:
        json.dump({'samples_ms': samples, 'outputs': outputs, 'warmups': warmups, 'runs': runs}, fh)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
