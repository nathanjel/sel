# PHP and JavaScript runtime measurements — 2026-09-20

Measured locally on PHP 8.5.10 (64-bit, GMP enabled, JIT disabled) and Node
v24.16.0. These are microbenchmarks, not application-wide speedup claims.
The baseline is the source snapshot immediately before these PHP scalar,
decimal-cache and JS guard edits, including the earlier structural-hash fixes.

Reproduce with `php tools/benchmark-php-runtime.php [baseline/bootstrap.php]`
and `node tools/benchmark-js-decimal-guard.mjs [baseline/decimal.mjs]`.
Omit the optional argument to measure the current implementation. The scripts
emit runtime details and all timing samples as JSON. PHP uses five samples;
JavaScript uses seven, with warmup. The figures below are medians in microseconds.

| Operation | Before | After |
| --- | ---: | ---: |
| PHP first scalar, ordinary record, 10 fields | 0.496 | 0.374 |
| PHP first scalar, ordinary record, 1,000 fields | 4.888 | 0.399 |
| PHP first scalar, ordinary record, 100,000 fields | 1409.667 | 0.401 |
| PHP first scalar, packed list, 100,000 values | 0.382 | 0.372 |
| PHP small decimal addition | 1.306 | 0.907 |
| PHP small decimal multiplication | 0.924 | 0.767 |
| PHP small decimal comparison | 1.037 | 0.598 |
| PHP small decimal division | 2.288 | 2.605 |
| PHP multiply/add chain | 2.084 | 1.859 |
| PHP compiled rounded multiply/add/divide expression | 9.895 | 10.061 |
| JS addition, 24-digit operand | 0.254 | 0.160 |
| JS addition, 1,000-digit operand | 2.276 | 0.192 |
| JS addition, 10,000-digit operand | 20.371 | 1.165 |
| JS addition, 100,000-digit operand | 195.023 | 10.679 |

PHP scalar access now reads the first packed slot or first ordinary child.
The latter previously allocated `array_values()` over the whole record. Packed
storage already benefited from PHP array sharing, explaining its small change.

PHP decimal descriptors carry a checked signed native mantissa, including a
cached null for magnitudes outside the native range. Matching source digits and
sign protect against stale caches when a caller changes a descriptor. Legacy
three-field descriptors still work. Native arithmetic results retain the computed
integer; equal-scale operands bypass multiplication by powers of ten. Canonical
digit strings remain eagerly available for the existing descriptor interface, so
this removes repeated input parsing, not every output conversion. Overflow still
falls back to exact GMP or extension-free arithmetic. Division and some signed
subtraction samples regress; the complete expression shows no measured benefit.
This is a targeted improvement to arithmetic chains, not a universal PHP speedup.

## Declared-property object experiment

The benchmark compares dynamically constructed three-field arrays with a class
having three declared readonly properties. Dynamic inputs prevent PHP from
reusing constant array literals. Array construction measured 0.116 µs versus
0.223 µs for the object. Retaining 10,000 values consumed 4,345,976 bytes for
arrays and 1,667,112 bytes for objects, including the holding array and strings.
Actual cached decimal descriptors also consumed 4,345,976 bytes in this sample;
extra fields fit the existing array allocation capacity and share digit strings.
These allocation sizes are specific to this PHP build and input set.

A separate four-property object with a native mantissa and deferred digits
performed the restricted multiplication probe in 0.277 µs. That probe omits the
full decimal guard, general fallback and public descriptor compatibility; it is
not directly comparable to `Dec::mul`. Objects therefore show a real memory
advantage and a promising lazy-arithmetic path, but their construction is slower
and a migration would change the array interface. The production change retains
arrays; this experiment does not establish that arrays are universally faster.

## JavaScript guard

For a nonnegative mantissa, shifting right by 3,321,928 bits yields zero exactly
when its bit length is below the existing conservative limit. This preserves
the previous branch condition without constructing a hexadecimal string or a
large precomputed threshold. Exact digit counting still runs near the limit.
Arithmetic remains entirely native BigInt. Tests replace `BigInt.toString` with
a throwing function to verify that below-cap addition and multiplication never
format their operands, then exercise the million-digit and fractional boundaries.

## Verification

- 21 focused PHP checks with GMP and under `php -n`; 14 JS guard checks.
- 39,990 decimal-oracle cases per lane, plus the same PHP oracle under `php -n`:
  zero mismatches.
- 911 conformance cases each for JS, PHP, both rebuilt JS bundles and PHP without
  GMP (`php -n -d extension=ctype`); the full PHP interpreter already uses ctype.
- 135 optimizer checks per lane; 31 PHP physical-layout checks.
- 4,000 differential programs across PHP, JS and both bundles: no disagreements
  or host crashes. All 54 PHP/JS host API probes agree.

The focused checks are wired into `tools/check.sh`. The full repository gate was
not rerun for these edits; its earlier Python join-alias disagreements remain
documented in the changelog.
