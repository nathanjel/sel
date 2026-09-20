# Python runtime optimization measurements (2026-09-20)

Baseline: a source snapshot after the structural-hash corrections, before these
Python runtime changes. Candidate: native UTF-8 decoding, a three-field decimal,
direct same-scale comparisons and cached math-plan opcode constants.

Measured on CPython 3.14.7, Linux x86-64. Values are medians of five repetitions
from `tools/benchmark-python-runtime.py`; parsing, compilation and context
construction are outside the timed regions. These are local microbenchmarks,
not application-wide speedup claims. The script emits every timing sample as JSON.

```sh
PYTHONPATH=/path/to/baseline/python python3 tools/benchmark-python-runtime.py > before.json
PYTHONPATH=python python3 tools/benchmark-python-runtime.py > after.json
```

## Decimal operations

The cached signed mantissa was another Python integer, with scale/bit-limit
checks on construction and dispatch. Removing it reduces `sys.getsizeof(Dec)`
from 64 to 56 bytes on this runtime. Alignment now scales only the operand that
needs it. Same-scale comparisons avoid alignment and the evaluator compares
signed integers directly, without retaining a second integer on every decimal.

Ratios below are baseline time / candidate time; values above 1 indicate faster
execution. Input pairs are recorded in the benchmark script.

| Input | Add | Subtract | Multiply | Divide | Compare |
|---|---:|---:|---:|---:|---:|
| small | 1.51× | 1.53× | 1.32× | 1.15× | 0.89× |
| mixed_sign | 1.54× | 1.45× | 1.34× | 1.19× | 1.50× |
| negative | 1.47× | 1.47× | 1.31× | 1.16× | 1.04× |
| unequal_scale | 1.27× | 1.39× | 1.31× | 1.14× | 1.18× |
| mixed_scale | 1.37× | 1.33× | 1.62× | 1.37× | 1.56× |
| large | 1.36× | 1.36× | 1.25× | 1.16× | 1.61× |

Small positive same-scale comparison alone is about 19 ns slower (160 → 179 ns).
Keeping the cache solely for that case would retain its construction and memory
cost for all arithmetic. The complete comparison-expression benchmark is essentially
unchanged (2.78 → 2.74 µs); the arithmetic workloads improve.

## UTF-8 and evaluation

| Operation | Before (µs) | After (µs) |
|---|---:|---:|
| `decode.ascii` | 1341.409 | 1.515 |
| `decode.unicode` | 6501.587 | 36.881 |
| `decimal.construct` | 0.885 | 0.633 |
| `ast.simple` | 3.346 | 2.975 |
| `plan.simple` | 3.345 | 2.752 |
| `program.simple` | 3.740 | 3.146 |
| `ast.chain` | 15.065 | 13.576 |
| `plan.chain` | 12.835 | 10.193 |
| `program.chain` | 13.029 | 10.656 |
| `ast.builtins` | 17.506 | 15.583 |
| `plan.builtins` | 13.127 | 9.743 |
| `program.builtins` | 13.697 | 10.256 |

Native strict decoding handles successful input without a Python byte loop or
code-point list. On `UnicodeDecodeError`, the original decoder supplies the same
SEL message and source position, outside the exception handler so no host
exception is chained into the result.

Math plans remain useful: the candidate plan is faster than the recursive AST
on each tested arithmetic workload. Resolving `IntEnum` attributes once removes
repeated metaclass lookups from dispatch without adding instructions or changing
evaluation order. Scratch storage remains per invocation to support nested or
reentrant evaluation safely.

## Fusion experiment

The fixed-expression fusion probe for `A * B` takes 2.012 µs,
versus 2.627 µs for the plan executor without the outer AST
depth wrapper. It combines loads and multiplication in one function, retaining
undefined-variable checks, decimal coercion and source positions. Both probes
use the same prepared context. This estimates remaining dispatch/scratch overhead
for one pure expression; it is not a general compiler or an application benchmark.

The production change keeps the existing instruction format. General fusion would
need additional specialization and coverage for arbitrary leaves, side effects,
intermediate range/rounding checks and compile-time cost. The measured dispatch
improvement is retained without introducing that machinery; the reproducible
fusion probe remains available for a future compiler experiment.

## Validation

- 585 Python unit tests, including 25 plan-versus-AST cases over multiple contexts.
- 911 Python conformance cases; 39,990 decimal-oracle cases with zero mismatches.
- 75,792 byte sequences compared with the original UTF-8 decoder: all single-
  and two-byte inputs plus 10,000 random inputs (seed 20260920). Identical
  text or error code, message and source position.
- 54 host API probes agree between Python and JavaScript.
- All 4,000 fuzz outputs match the pre-change Python snapshot byte for byte.
  The existing 11 Python/JavaScript join-alias disagreements remain; they are
  not introduced or fixed by these optimizations.
