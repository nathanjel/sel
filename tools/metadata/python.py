"""Focused checks, or `... python.py bench`; SEL_METADATA_ROOT selects a source tree."""
import gc
import json
import os
from pathlib import Path
import statistics
import sys
import time
import tracemalloc

root = Path(os.environ.get('SEL_METADATA_ROOT', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(root / 'python'))
from sel import compile as compile_sel
from sel.value import Value, structural_hash, _SHAPES
from sel import decimal as D

leaf = Value.text('1')
ctx = Value.none()
ctx.set('X', leaf)
ctx.set('Y', Value.text('two'))
program = compile_sel('RECORD("id", X, "name", Y)')
program.run(ctx)

def churn(n):
    for i in range(n):
        Value.record(['field_' + str(i), 'id'], [leaf, leaf])

def aliases(n):
    join = compile_sel('LINK(L, R, O, C, O["id"] == C["id"])')
    context = Value.none()
    context.set('R', Value.list([Value.record(['id'], [leaf])]))
    for i in range(n):
        context.set('L', Value.list([Value.record(['id', 'alias_' + str(i)], [leaf, leaf])]))
        join.run(context)

if len(sys.argv) > 1 and sys.argv[1] == 'bench':
    samples = []
    for _ in range(5):
        start = time.perf_counter()
        for i in range(20000):
            program.run(ctx)
        samples.append((time.perf_counter() - start) * 1e6 / 20000)
    power_samples = {}
    for exponent in (100, 10000):
        D._pow10(exponent)
        times = []
        for _ in range(5):
            tick = time.perf_counter()
            for i in range(1000):
                D._pow10(exponent)
            times.append((time.perf_counter() - tick) * 1e6 / 1000)
        power_samples[exponent] = times
    tracemalloc.start()
    gc.collect()
    start = tracemalloc.get_traced_memory()[0]
    churn(20000)
    gc.collect()
    shapes = tracemalloc.get_traced_memory()[0] - start
    aliases(5000)
    gc.collect()
    both = tracemalloc.get_traced_memory()[0] - start
    before_powers = tracemalloc.get_traced_memory()[0]
    for n in range(10000, 12000):
        D._pow10(n)
    gc.collect()
    powers = tracemalloc.get_traced_memory()[0] - before_powers
    print(json.dumps(dict(runtime=sys.version, steady_us=samples,
                         shapes_retained=shapes, shapes_aliases_retained=both,
                         powers_retained=powers, power_us=power_samples)))
else:
    held = Value.record(['held'], [leaf])
    prepared = program.run(ctx).shape
    churn(2000)
    again = Value.record(['held'], [leaf])
    assert held.shape is not again.shape and held.eql(again)
    assert structural_hash(held) == structural_hash(again)
    assert program.run(ctx).shape is prepared
    assert len(_SHAPES) <= 256
    for keys in [['wide_' + str(i) for i in range(257)], ['x' * 16385]]:
        assert Value.record(keys, [leaf] * len(keys)).shape is not Value.record(keys, [leaf] * len(keys)).shape
    dynamic = compile_sel('RECORD(K, X)')
    ctx.set('K', Value.text('first'))
    assert dynamic.run(ctx).get('first').scalar == '1'
    ctx.set('K', Value.text('second'))
    assert dynamic.run(ctx).get('second').scalar == '1'
    assert compile_sel('RECORD("id", X, "id", Y)').run(ctx).get('id').scalar == 'two'
    aliases(1000)
    from sel.builtins.structure import _ALIAS_PLANS
    assert len(_ALIAS_PLANS) <= 256
    for n in range(65, 300):
        assert D._pow10(n) == 10 ** n
    assert D._pow10(10000) == 10 ** 10000
    for n in range(100000, 400000, 20000):
        assert D._pow10(n) == 10 ** n
    assert D._pow10(1000001) == 10 ** 1000001
    assert len(D._POW10) <= 64 and max(D._POW10) <= 1000000
    assert sum(D._POW10) <= 1048576
    print('Python metadata checks passed')
