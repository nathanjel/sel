# Python native arithmetic and metadata measurements

Run from the repository root, with no competing benchmarks or builds:

```sh
mkdir -p /tmp/sel-python-runtime/before
git archive 6815faa | tar -x -C /tmp/sel-python-runtime/before
python3 tools/python-runtime/compare.py \
  --before /tmp/sel-python-runtime/before \
  --output tools/python-runtime/results
```

The baseline is the complete `6815faa` tree. The driver runs baseline, candidate,
candidate repeat and baseline repeat sequentially, using one interpreter and
`PYTHONHASHSEED=0`. The six scenarios use the shared 137,100-row fixture and
SQL/result oracle, seven measured samples and three warmups. Normal GC remains
enabled; parsing and fixture setup are outside prepared timings. Every result
and SQL/continuation plan is checked, as is context integrity after each sample.
Mandelbrot uses eleven samples and three warmups; every frame must match the
historical SHA-256. The baseline and candidate scale harnesses must be identical.

Each tree also runs the existing native-arithmetic/UTF-8 microbenchmark, the
shared-scale arithmetic probe, and the metadata churn probe. Metadata timings
are uninstrumented, after one warm pass; activity counting and `tracemalloc`
retained/peak measurements are separate passes. Entry churn exceeds 64 powers
or 256 shapes; weighted churn uses eight exponents around 160,000 (fewer than
64 entries, but more than the 1,048,576 exponent-weight budget). The diagnostic
checks exact powers, shape field lookup and bounds at every step. Hot/cold mixes
expose clear-all eviction. They characterize the current bounded caches, not a
new cache policy or a guarantee for every workload.

`power-profile-*.json` counts power lookups/misses over three Mandelbrot frames;
it is instrumentation, not a latency measurement. JSON stores individual timing
samples. `summary.json` contains application medians/means. Logs are ignored by
Git. Timings are local observations, not cross-machine performance guarantees.

Small full-batch S4/S6 slowdowns prompted a separate comparison with fifteen
samples and three warmups, without the other scenarios' preceding heap history:

```sh
python3 tools/python-runtime/targeted.py \
  --before /tmp/sel-python-runtime/before --output tools/python-runtime/results
python3 tools/python-runtime/activity.py > tools/python-runtime/results/activity.json
```

The activity probe is untimed: it counts calls to the two changed arithmetic
functions only during prepared execution and verifies rows against the oracle.
It does not count parsing, fixture creation or compilation.

Validation:

```sh
PYTHONPATH=python .venv/bin/python -m pytest python/tests -q
PYTHONPATH=python python3 python/bin/conformance.py
PYTHONPATH=python python3 python/bin/sqlt
python3 tools/metadata/python.py
python3 tools/decimal-oracle.py 4000 20260813 > /tmp/python-decimal-oracle.txt
PYTHONPATH=python python3 python/bin/check-decimal.py /tmp/python-decimal-oracle.txt
SEL_IMPLS='python cpp js' tools/fuzz.sh 4000 20260813
SEL_IMPLS='python cpp js' tools/check-api.sh
```

The recorded fuzz run has **11 pre-existing join-output disagreements**, not a
clean cross-language pass. To verify they predate this patch, generate the same
corpus with `node tools/gen-programs.mjs 4000 20260813`, run `python/bin/batch.py`
in each tree with its corresponding `PYTHONPATH`, and compare complete outputs.
They match byte-for-byte; corpus/output hashes and the disagreement log are saved.
