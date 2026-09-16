# F1–F6 implementation and verification — 2026-09-16

Scope: the five requested fixes (F1–F4 and F6), starting in Lisp and rolled out
to Python, JS, PHP and C++. F5 was already addressed by the parallel remediation
work and its original witness was rechecked. No commit was made. Unrelated
worktree changes and the historical audit JSON were preserved.

Status: the requested implementations and live regressions pass. The refreshed
full-roster default `tools/check.sh` run finished **ALL GREEN** in 586 seconds.
All 192 mutations were caught: 185 in that run and the seven database-dependent
entries in separate live runs, with zero survivors or suite errors. The separate
database-backed SQL fuzz boundary under C1 remains open.

## Delivered behavior

| Item | Result |
|---|---|
| F1 | TAKE/DROP compose as bounded slices; repeated/zero/large bounds cannot widen an earlier slice. |
| F2 | MariaDB uses NO PAD text identity; MySQL has its own matching collation. SQLite explicitly overrides NOCASE/RTRIM with BINARY. TEXT DISTINCT and direct derived group keys retain exact identity. |
| F3 | Stored NUM key representation survives direct projection/grouping. Unproved computed keys remain local; the planner tracks the fields actually used for identity rather than blocking unrelated computed measures. |
| F4 | Duplicate or SQL-case-colliding RECORD aliases retain local evaluation, preserving overwrite order and errors rather than emitting ambiguous SQL columns. |
| F5 | Joined prefixes lacking SEL's nested binder records remain local until a safe projection; the original paginated-join witness passes. |
| F6 | A schema-declared unique, non-null NUM revision key enables descending TOP 1 per direct partition. SQL transfers full winning rows; the original small local continuation reconstructs nested output. |

Additional integration corrections: direct projected column types are retained
without treating guarded/raw SQL fields as proven native types; UNKNOWN group
keys are refused. A partially local MAP cannot absorb a downstream FILTER,
which would lose retained ordinal keys when its continuation renumbers rows.

## Reproducible verification

Run `bash tools/adversarial/regressions.sh`. This builds the C++ adapter, installs
a fresh Python wheel and disposable PHP/Lisp tooling, provisions pinned database
images without published ports or persistent volumes, runs the checks below,
and removes only the containers it created.

| Harness | Live SQL executions | Exact hybrid replays | Coverage |
|---|---:|---:|---|
| `identity.py` | 1,008 | 504 | Fourteen direct/derived grouping, DISTINCT and filter scenarios; SQLite NOCASE/RTRIM, PostgreSQL numeric scale, MariaDB trailing spaces. |
| `witnesses.py` | 1,128 | 468 | Thirteen original F1–F5 witnesses and controls, including cases choosing safe memory fallback. |
| `latest.py` | 432 | 216 | Six normalized/EAV, empty/filtered, complete-member and scale scenarios. |
| Total | **2,568** | **1,188** | Five source hosts plus a freshly installed wheel; PostgreSQL/MariaDB/SQLite, strict off/on and inline/native parameters. |

Group membership tests compare exact record multiplicity, not unspecified SQL
group order. Explicitly ordered slice and latest-member tests check order too.
The 100,000-revision EAV test transfers **100 complete winner rows**, a 1,000×
reduction in input transfer; this is not a speedup or database-work complexity
claim. The F6 strategy does not implement general per-group TOP N.

The full expression/row/statement oracle also passes on PostgreSQL 17.10,
MariaDB 11.8.8, MySQL 8.4.11 and PDO SQLite 3.46.1: 31 successful statement
cases on each server platform and 30 on SQLite, each executed inline and with
native parameters; one SQLite numeric-coercion case remains a declared refusal.
Python's additional live SQLite tests use SQLite 3.51.2.

The freshly installed wheel separately passes all 852 shared SQL cases, 898
conformance cases and 552 Python tests (the test run asserts the imported
package path is the installed wheel, not the source checkout). The default
integration gate covers both freshly built JS bundles as well as the five source
implementations. Added regression sources include conformance files 16–20,
SQL cases 27–41, four focused Python test modules, and mutation entries for
latest-member recognition, derived key identity, SQLite collation and mixed-MAP
filter safety. The gate passed 898 conformance cases in each of seven variants,
852 SQL cases in every source host, 552 Python tests, 428 Lisp checks and 129
C++ core plus 85 SQL unit checks. Cross-host fuzzing agreed on 4,000 local and
2,000 SQL programs (four dialects); each decimal core passed 39,990 oracle cases.
The original 20 local audit scenarios additionally agree across all eight
source/package variants, including error codes and positions using the standard
Lisp batch adapter.

Evidence from the clean disposable-script run is retained locally:

- `/tmp/sel-identity-evidence.kaejhym2/results.json`
- `/tmp/sel-witness-evidence.xizbic6x/results.json`
- `/tmp/sel-latest-evidence.g2zzmqaq/results.json`
- `/tmp/sel-live-regressions-0916.log`
- `/tmp/sel-integration-0916-default-final.log`
- `/tmp/sel-mutations-live-0916.log` and `/tmp/sel-mutations-ansi-live-0916.log`

These paths are run-local evidence, not committed artifacts; the reproduction
script creates fresh uniquely named evidence directories on another machine.

## Explicit remaining boundaries

- F6 requires caller/schema uniqueness and NOT NULL guarantees; raw/correlated
  inputs, composite partitions, computed keys, TOP 0/N>1, additional member
  aggregates, named binders and incompatible source ordering retain fallback.
- Computed projection types are not generally propagated. Regrouping a projected
  COUNT currently retains a grouped SQL prefix and performs the next group locally.
- SQL validation/error preservation is **not** solved by these fixes. In addition
  to the documented numeric/error differences, database-backed SQL fuzzing finds
  five depth-boundary expressions per target. Local SEL raises `E_DEPTH`, while
  SQL normalization accepts them. This reproduces in an extraction of HEAD
  `01d9387`; see [C1](sel-gaps-2026-09-15-07-sql-validation-error-contract.md).
  Do not describe that optional database fuzz lane as green.

The [work index](sel-gaps-2026-09-15-00-index.md) links each issue's rationale,
code lanes and acceptance criteria; the [harness README](../../tools/adversarial/README.md)
distinguishes desired-behavior regressions from the historical bug-asserting audit.
