# Regression tests from the Go review — 2026-09-29

Added 18 shared cases in `conformance/23-runtime-regressions.selt`:

- Two record-shape identity cases involving NUL-containing keys. Both detect
  corruption through ordinary lookups without aborting the runner with a host panic.
- Six oversized integer argument cases, covering rejection and clamping.
- Three depth-restoration cases, covering both coalescing operators and both
  missing-variable and missing-key failures.
- Seven scope-restoration cases covering SORT, TOP, BUCKET, shadowed variables,
  and assignment after recovery.

Added two Go tests in `go/sel/concurrency_test.go`, enabled with the `race` build
constraint. Both `go/Makefile` and the Go unit lane in `tools/impls.sh` now run
`go test -race ./...`. Each worker owns its context and values. One test evaluates
independent programs using the decimal cache; the other shares a compiled program
while varying record shape and field position. Both check returned values as well
as allowing the race detector to inspect runtime memory access.

Initial results for the new cases, before fixes:

| Configuration | Passed | Failed |
| --- | ---: | ---: |
| JavaScript source | 18 | 0 |
| JavaScript bundle | 18 | 0 |
| JavaScript minified bundle | 18 | 0 |
| PHP | 18 | 0 |
| C++ | 18 | 0 |
| Common Lisp | 16 | 2 |
| Python source | 18 | 0 |
| Python installed wheel | 18 | 0 |
| Go | 0 | 18 |

Lisp fails `recovery.scope.top` and `recovery.scope.top-shadow`: TOP leaves its
binder active after a missing-variable error recovered by coalescing. This is
an additional affected implementation of the scope-restoration finding.

Go fails all 18 new cases. Its two concurrency tests also fail under the race
detector: ten race reports for independent decimal evaluations and four for the
shared compiled program. No incorrect result was observed in those concurrent
runs; the race reports themselves are failures.

The full conformance suite contains 1,091 cases. JavaScript source and both
bundles, PHP, C++, Python source, and the installed Python wheel pass all 1,091.
Go passes the existing 1,073 and fails the 18 added cases. Lisp passes 1,089
and fails the two TOP scope cases. Every runner reports zero suite errors.

Rust was attempted separately because it is under development and is not in the
default roster. `cargo build --bin conformance --offline` succeeds, but
`rust/src/bin/conformance.rs` contains only `fn main() {}`. Its silent zero exits
on the new and full suites therefore do not validate any case; Rust is untested.

Commands (from repository root, after rebuilding the relevant artifacts):

```sh
. tools/impls.sh
# Repeat for each configuration in the table:
impl_conformance go conformance/23-runtime-regressions.selt
impl_conformance go conformance/*.selt
# Isolate the concurrency failures so either test can report independently:
(cd go && go test -race ./sel -run '^TestConcurrentIndependentDecimalEvaluations$' -count=1)
(cd go && go test -race ./sel -run '^TestConcurrentSharedProgramFieldCache$' -count=1)
```

Logs from this run are in `/tmp/sel-review-test-results/`. All 1,091 conformance
case names are unique and `git diff --check` passes. At that stage the runtime implementations were not fixed; the failing
regressions were intentional. SQL, live database, fuzz, and unrelated host unit
suites were not rerun for the test-only change. The repair results follow below.


## Repairs and verification

The Go runtime now encodes record-shape cache keys with length prefixes, so NUL
characters cannot hide key boundaries. Decimal size conversion saturates before
narrowing instead of wrapping, preserving both range rejection and slicing clamps.
The global power-of-ten cache uses a read/write mutex for access and accounting;
compiled field lookups atomically publish immutable shape/slot pairs and use one
snapshot per lookup. Evaluation restores its depth and frame stack on every exit,
including panics caught by coalescing or join prefilters.

Common Lisp TOP had its `ctx-pop-frame` outside the `unwind-protect` because of
a misplaced closing parenthesis. The cleanup is now inside the protected form.

Both implementations pass the 18 added regression cases. The original Go panic
reproduction now returns `E_NO_KEY` as specified. `cd go && make test` passes:
all Go unit tests with `-race`, 1,091 conformance cases, and 1,065 SQL cases.
The two concurrency tests also passed in the initial targeted validation.

Common Lisp also passes its full validation: `lisp/bin/conformance
conformance/*.selt` reports 1,091 passing cases, `lisp/bin/test` reports 599
passing checks, and `lisp/bin/sqlt` reports 1,065 passing SQL cases. There are
zero failures or suite errors. `git diff --check` passes.

Fix validation logs are in `/tmp/sel-fix-results/`. Rust was excluded from the
repair work. Benchmarks and live database tests were not rerun.
