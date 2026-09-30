#!/usr/bin/env python3
"""cProfile of one scale-test scenario's program.run() on a given tree.

  python3 tools/perf/python/profile_scale.py TREE_ROOT scenarioN [top]

Prints the top functions by own time (tottime) and by cumulative time, with the
call counts: diff the output of a 0.9.2 tree against the current tree to see
where the extra time went.
"""
import cProfile, importlib.util, json, pstats, sys
from pathlib import Path

tree = Path(sys.argv[1]).resolve()
scenario = sys.argv[2]
top = int(sys.argv[3]) if len(sys.argv) > 3 else 25
sys.path.insert(0, str(tree / 'python'))
sys.path.insert(0, str(tree / 'tools' / 'scale-test'))
spec = importlib.util.spec_from_file_location('sel_benchmarks', tree / 'tools/scale-test/sel_benchmarks.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.register_benchmark_builtins()
dataset = mod.read_json(tree / 'tools/scale-test/dataset-10x.json', parse_float=str)
reference = json.load(open(tree / 'tools/scale-test/benchmark_results.json'))
query = next(x['query'] for x in reference if x['id'] == scenario)
context = mod.load_context(dataset)
from sel import compile
program = compile(query)
program.run(context)            # warm: optimiser, plans, caches
pr = cProfile.Profile()
pr.enable()
program.run(context)
pr.disable()
st = pstats.Stats(pr)
st.sort_stats('tottime').print_stats(top)
st.sort_stats('cumulative').print_stats(top)
