#!/usr/bin/env python3
"""Separate timing, cache-activity and retained-memory probes; run from a checkout."""
import gc
import json
from pathlib import Path
import statistics
import sys
import time
import tracemalloc
sys.path.insert(0, str(Path.cwd() / 'python'))
from sel import decimal as D
from sel.value import Value, _SHAPES

# Cyclic sets cross each budget independently. Interleaving a hot key with cold
# keys also exposes clear-all eviction without pretending this is an LRU cache.
workloads = {
    'powers.stable': [100, 1000, 10000] * 100,
    'powers.entry_churn': list(range(100, 165)) * 10,
    'powers.weight_churn': list(range(160000, 160008)) * 3,
    'powers.hot_and_cold': [n for i in range(1000, 1300) for n in (100, i)],
    'shapes.stable': ['id', 'name', 'address'] * 100,
    'shapes.entry_churn': ['field_' + str(i) for i in range(257)] * 10,
    'shapes.hot_and_cold': [n for i in range(1000) for n in ('id', 'field_' + str(i))],
}
leaf = Value.text('1')

def reset():
    D._POW10.clear()
    D._POW10_WEIGHT = 0
    _SHAPES.clear()

def run(name, keys, diagnostic=False):
    misses = clears = 0
    power = name.startswith('powers.')
    cache = D._POW10 if power else _SHAPES
    for key in keys:
        if diagnostic:
            signature = key if power else (key,)
            misses += signature not in cache
            size = len(cache)
        if power:
            result = D._pow10(key)
            if diagnostic:
                assert result == 10 ** key
                assert len(cache) <= 64 and sum(cache) <= 1048576
                assert D._POW10_WEIGHT == sum(cache)
        else:
            result = Value.record([key], [leaf])
            if diagnostic:
                assert result.get(key) is leaf and len(cache) <= 256
        if diagnostic:
            clears += len(cache) < size
    return dict(calls=len(keys), misses=misses, clears=clears, entries=len(cache),
                weight=sum(cache) if power else None)

out = {}
for name, keys in workloads.items():
    samples = []
    for _ in range(5):
        reset()
        run(name, keys)
        start = time.perf_counter()
        run(name, keys)
        samples.append((time.perf_counter() - start) * 1e6 / len(keys))
    reset()
    diagnostic = run(name, keys, True)
    reset()
    gc.collect()
    tracemalloc.start()
    start = tracemalloc.get_traced_memory()[0]
    run(name, keys)
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    out[name] = dict(median_us=statistics.median(samples), samples_us=samples,
                     diagnostic=diagnostic, retained_bytes=retained-start, peak_bytes=peak-start)
print(json.dumps(dict(runtime=sys.version, results=out), indent=2))
