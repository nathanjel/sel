# C++ collection allocation comparison

Compare the same scale harness in two source trees. The baseline for the recorded
results is `c5a8991`. Archive it into a separate directory, then copy the current
`tools/scale-test/sel_benchmarks.cpp` into the matching path there. Keep the
baseline's runtime sources and headers together. Build **both** trees before
starting any timing:

```sh
make -C /tmp/sel-before/cpp -j2 build/scale-bench build/sel.o
make -C cpp -j2 build/scale-bench build/sel.o
python3 tools/cpp-collection/benchmark.py --before /tmp/sel-before \
  --output /tmp/sel-collection-results
```

The runner executes baseline, candidate, candidate, baseline sequentially, with
three warmups and seven measured iterations per scenario. It checks the same
current reference in every run. Fixture loading, compilation and context cloning
are outside prepared execution time; the raw reports retain clone timing.

Compile `tools/commit-benchmark/mandelbrot.cpp` separately against each tree's
`sel.o` and include directory. Run each from the repository root with
`MANDEL_RUNS=11 MANDEL_WARMUPS=3`, passing an output JSON filename. Compare every
output frame, as well as the median time. Run in the same reversed order.

For glibc allocation accounting:

```sh
c++ -std=c++23 -O2 -Icpp tools/cpp-collection/memory.cpp \
  cpp/build/sel.o -o /tmp/sel-memory
/tmp/sel-memory text
/tmp/sel-memory num
/tmp/sel-memory rows
```

Build the same probe against the baseline's headers and object. Repeat each mode
five times in fresh processes. The probe reserves its outer vector before taking
the starting allocation count, constructs 200,000 values, then clears them. It
reports live allocator bytes and bytes still allocated after clearing, including
allocator rounding and caches. These are not RSS or portable allocator metrics.
Construction/destruction times are secondary microbenchmarks.

The recorded raw-block pool experiment is preserved as a patch and measurements
under `docs/interim/cpp-collection`; it was rejected and is **not** part of the
runtime. The combined-allocation implementation has no new cache.
