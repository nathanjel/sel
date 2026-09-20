# PHP scalar-access follow-up

Run sequentially with no competing benchmarks/builds:

```sh
mkdir -p /tmp/sel-php-runtime/before
git archive 6815faa | tar -x -C /tmp/sel-php-runtime/before
python3 tools/php-runtime/compare.py \
  --before /tmp/sel-php-runtime/before --output docs/interim/php-runtime
```

Only `php/src/Builtins/Structure.php` changes in the measured PHP runtime.
The completed, uncommitted Python work is not part of this comparison.
The current/baseline scale harnesses are identical. Both use the same 137,100-row
fixture and SQL/result oracle, three warmups, seven measured samples and normal
GC. Prepared timing excludes parsing/setup. SQL/continuation checks and all row
results must pass; the harness checks input integrity after each sample.

GMP order: baseline, candidate, candidate repeat, baseline repeat. A separate
S4/S6 baseline/candidate comparison uses fifteen samples and three warmups,
without the earlier scenarios' heap/JIT history. Fallback order: baseline then
candidate, all six scenarios. Each full batch also runs eleven Mandelbrot frames
with three warmups and verifies the historical output hash.

Both extension configurations use `php -n -d extension=ctype`, unlimited memory,
CLI OPcache, a 128M JIT buffer and `opcache.jit=1255`. The GMP configuration adds
only `-d extension=gmp`. This PHP build includes OPcache even under `-n`; the
runner asserts JIT is on and records extensions, ini values and JIT status in
`runtime-*.json`. On a build where OPcache is a separate module, load it explicitly
before using this runner. Comparisons are local to this configuration, not a
claim that all PHP deployments get the same result.

The focused probes time cached property/getter reads, 500-row numeric/text/lazy
literal joins, and boolean sorting. They verify results outside timing, warm
three executions and report seven samples. Both getter spellings are measured
in each source tree; only internal Structure reads differ. Returning rows and
sorting still allocate, so a getter microbenchmark is not a query speedup.

`scaled-key-control.php` is a separate untimed investigation of mixed numeric
representations; run it from either source tree. It is not part of timed runs.

Correctness commands (timings are not collected while these run):

```sh
php php/tests/a5.php
php tools/check-php-runtime.php
php tools/check-php-optimizer.php
php tools/metadata/php.php
php php/bin/conformance
php php/bin/sqlt
python3 tools/decimal-oracle.py 4000 20260813 > /tmp/php-decimal-oracle.txt
php tools/check-decimal.php /tmp/php-decimal-oracle.txt
php -n -d extension=ctype php/tests/a5.php
php -n -d extension=ctype tools/check-decimal.php /tmp/php-decimal-oracle.txt
SEL_IMPLS='php cpp js' tools/fuzz.sh 4000 20260813
SEL_IMPLS='php cpp js' tools/check-api.sh
```
