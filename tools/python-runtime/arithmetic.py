#!/usr/bin/env python3
"""Focused scale-cancellation benchmark. Inputs and correctness checks untimed."""
import json
from pathlib import Path
import statistics
import sys
import timeit
sys.path.insert(0, str(Path.cwd()/'python'))
from sel import decimal as D
results = {}
for scale in (0, 2, 1000, 10000):
    a, b = D.make(False, 7, scale), D.make(False, 2, scale)
    assert D.format(D.div(a, b)) == '3.5'
    for op in ('sub', 'div'):
        fn = getattr(D, op)
        count = 200 if scale == 10000 else 2000
        samples = [t * 1e6/count for t in timeit.repeat(lambda: fn(a,b), number=count, repeat=7)]
        results[f'{op}.shared_scale_{scale}'] = dict(median_us=statistics.median(samples), samples_us=samples)
print(json.dumps(results, indent=2))
