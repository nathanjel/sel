# Go SEL implementation review — 2026-09-29

Reviewed checkout: `faff480b60277575f5087fd263aa4be87e61195b`.
Environment: Go `go1.26.8-X:nodwarf5`, Linux/amd64.

The implementation passes the existing suites, but targeted validation found six
correctness and concurrency defects. Benchmark results should not be treated as
validation of these behaviors. No implementation changes were made in this review.

Validation completed:

- `cd go && make test`: Go unit tests pass; 1,073 conformance cases pass;
  1,065 SQL cases pass (639 also checked against a mirrored dialect, 12 refused
  by the type system).
- Targeted source-level reproductions below, with JavaScript comparisons for
  record keys, scope restoration, depth restoration, and large size arguments.
  The specification, rather than agreement with JavaScript, determines the
  expected behavior.
- Two standalone concurrent workloads under `go run -race`: both report races.

Review concentrated on evaluation, decimal conversion, value representation,
optimization, and aggregate scopes. The SQL suite was run; this is not a full
independent audit of SQL translation or live database execution. This is an
engineering review with executable evidence, not a mathematical proof.

1. **[P1] Record shape cache keys collide for legal text keys.**
   Location: `go/sel/shape.go:39–44`.

   `strings.Join(keys, "\x00")` does not encode key boundaries unambiguously.
   `["a", "b"]` and `["a\x00b"]` reuse the same shape, despite different field
   counts. The cache is global, so prior evaluations can also trigger the bug.

   ```sel
   A = RECORD("a", 1, "b", 2); RECORD("a\u{0}b", 3)["b"]
   ```

   Expected: `E_NO_KEY`. Actual: Go index-out-of-range panic escaping `Program.Run`.
   Merely dumping the second record also panics. TEXT permits U+0000, and record
   keys preserve their text identity (§3 and §7). Encode each key with its length
   or use another collision-free tuple encoding; do not reject legal keys.

2. **[P1] Decimal-to-integer conversion silently wraps before range checks.**
   Location: `go/internal/decimal/dec.go:246–252`; consumers include
   `go/sel/eval.go:15–28` and `go/sel/args.go:87`.

   `big.Int.Int64()` is called without checking representability. The subsequent
   checks examine the truncated value, allowing oversized and negative arguments
   through and changing collection/text slicing behavior.

   | Source | Expected | Actual |
   | --- | --- | --- |
   | `POWER(2,18446744073709551616)` | `E_RANGE` | `1` |
   | `ROUND(1.25,18446744073709551616)` | `E_RANGE` | `1` |
   | `ROUND(1.25,-18446744073709551616)` | `E_RANGE` | `1` |
   | `LEFT("abc",18446744073709551617)` | `abc` | `a` |
   | `TAKE((1,2,3),18446744073709551617)` | all three elements | first element |

   Check sign and bounds in arbitrary precision before conversion. For slicing,
   clamp against the actual collection length where the function contract calls
   for clamping. The scale/exponent caps are normative in §6.4.

3. **[P1] Independent evaluations race on the global decimal cache.**
   Location: `go/internal/decimal/dec.go:68–83`.

   `Pow10` reads and mutates `pow10Cache` and `pow10Weight` without synchronization.
   Separate programs and separate contexts still share these globals. Eight
   goroutines evaluating `ROUND(1, n)` for scales between 20 and 56 produced ten
   race reports, including map reads/writes and the cache accounting variable.
   Concurrent Go map access can terminate the process; this is not a recoverable
   SEL error. Synchronize lookup, insertion, eviction, and accounting together,
   and safely publish immutable cached integers.

4. **[P2] A shared compiled program races on its field-slot cache.**
   Location: `go/sel/eval.go:83–95`.

   Concurrent runs of the same `Program` write `node.SlotCache` while other runs
   read it. The shape test and subsequent slot lookup also read the field
   separately, so they need not observe the same cache entry. Eight goroutines
   running `R["x"]` with independently allocated contexts and varying record
   shapes produced four race reports. `Program.physOnce` protects optimization
   only; it does not protect these later mutations.

   Keep this cache per evaluation, or safely publish and load one immutable cache
   entry atomically and use that same snapshot for both the shape and slot.
   This finding concerns concurrent program reuse; it does not assume that callers
   concurrently mutate a shared input context.

5. **[P2] Caught missing-value errors permanently consume evaluation depth.**
   Location: `go/sel/eval.go:31–45`.

   The decrement runs only on normal return. An `E_UNDEF_VAR` or `E_NO_KEY`
   recovered by `??`/`???` leaves the failed node and intervening nodes counted
   as active. A shallow MAP over 200 items with body `MISSING ?? 0` fails with
   `E_DEPTH`, although each iteration should return zero. This violates §6.4's
   nesting limit and the recovery semantics in §5.

   Generate the reproduction from the repository root:

   ```python
   import subprocess
   source = 'MAP((' + ','.join(['1'] * 200) + '), MISSING ?? 0)'
   subprocess.run(['go/build/sel', '-e', source])
   ```

   Expected: 200 zero values. Actual: `E_DEPTH` at column 419. Restore the depth
   counter during panic unwinding as well as normal returns.

6. **[P2] Aggregate frames survive errors recovered by coalescing.**
   Location: `go/sel/builtins_aggregate.go:226–228`, also TOP at 320–322 and
   BUCKET at 399–427. Join paths contain similar unprotected push/pop pairs.

   ```sel
   (SORT((1,2), X, MISSING) ?? 0); X
   ```

   Expected: `E_UNDEF_VAR` for the final `X`. Actual: `1`.

   ```sel
   (BUCKET((1,2), MISSING) ?? 0); _
   ```

   Expected: `E_UNDEF_VAR` for the final `_`. Actual: `1`.

   The panic skips `PopFrame`, and the surrounding coalescing operator resumes
   evaluation with an aggregate binder still installed. Besides incorrect reads,
   this can shadow a context variable or make later assignments fail with
   `E_BAD_ASSIGN`. §6.1 limits aggregate bindings to their evaluation scope.
   Restore the previous frame-stack length on every exit, including panics, and
   audit the other push/pop pairs. This is separate from the depth-counter defect.

The concurrency reproducer is in `go-review-race.go` beside this document. Run
from `go/`:

```sh
go run -race ../docs/interim/2026-09-29/go-review-race.go
go run -race ../docs/interim/2026-09-29/go-review-race.go slot
```

Both commands are expected to fail under the reviewed implementation. After
repairs, add durable conformance regressions for findings 1, 2, 5, and 6 and Go
race tests for findings 3 and 4, then rerun the benchmark to measure any cost of
restoring correct behavior.
