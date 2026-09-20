#!/usr/bin/env python3
"""Untimed direct arithmetic-call counts for the two small application slowdowns."""
import importlib.util
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/scale-test'))
spec = importlib.util.spec_from_file_location('bench', ROOT/'tools/scale-test/sel_benchmarks.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
from sel import decimal as D
bench.register_benchmark_builtins()
context = bench.load_context(bench.read_json(ROOT/'tools/scale-test/dataset-10x.json', parse_float=str))
report = {}
for case in bench.read_json(ROOT/'tools/scale-test/benchmark_results.json'):
    if case['id'] not in ('scenario4','scenario6'):
        continue
    program = bench.compile(case['query'])
    counts = dict(sub=0,div=0)
    originals = {name:getattr(D,name) for name in counts}
    def wrap(name):
        def call(*args,**kwargs):
            counts[name] += 1
            return originals[name](*args,**kwargs)
        return call
    for name in counts: setattr(D,name,wrap(name))
    try:
        _,_,_,rows = bench.run_and_materialize(program,context)
        assert bench.same_value(rows,case['in_memory_rows'])
    finally:
        for name,fn in originals.items(): setattr(D,name,fn)
    report[case['id']] = counts
print(json.dumps(report,indent=2))
