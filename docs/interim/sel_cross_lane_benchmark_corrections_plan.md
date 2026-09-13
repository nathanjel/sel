# SEL Cross-Lane Benchmark Corrections and Comparability Plan

## Status and scope

This is a reviewed implementation plan and implementation record. The
observations in **Verified current behavior** describe the pre-correction
repository. The steady-state and persistent-database portions of the **Target
contract** are now implemented; the evidence and remaining optional scope are
recorded below.

The suite covers six scenarios over the 10x fixture (currently 137,100 source
rows) in five in-memory hosts—Common Lisp, C++, JavaScript, PHP, and Python—and
two database lanes—PostgreSQL and MariaDB. Database timings are useful beside
host timings, but they are not the same metric and must not be presented as an
unqualified seven-way engine ranking.

## Implementation status (2026-09-13)

Implemented in `tools/scale-test/benchmark_all.py`, the five host runners, and
the persistent PDO database client:

- schema-versioned reports retain fixture SHA-256/table counts, raw phase
  samples, reference SHA-256/scenario identity, runtime/JIT/compiler metadata,
  representation counters, parity, and supported statistics;
- the default steady-state run compiles each selected program once, performs
  two warm-ups and ten measured cycles in one host process, separates
  `program_run_ms`, `materialize_ms`, and `prepared_total_ms`, and verifies
  context immutability outside the timers;
- all five in-memory lanes use the same selected fixture and report `137100`
  shaped records, one intentional root fallback record, and five list values on
  the 10x fixture;
- PostgreSQL and MariaDB use one persistent PHP/PDO client each, execute the
  original generated SQL without a PostgreSQL-only JSON wrapper, and measure
  execute/fetch, continuation, and hybrid-total phases separately; hybrid
  continuations are generated SEL programs rather than hand-written branches;
- the wrapper streams progress and rejects wrong scenario identity, fixture
  identity, reference identity, representation counts, context/parity gates,
  phase values, or sample counts; C++ is rebuilt with make -B before the
  measured binary is used;
- fixture setup uses direct packed/vector storage for the common shaped-record
  rows in C++, JavaScript, PHP, and Python, avoiding per-field entry-pair
  materialization on the preparation path;

Evidence from the corrected default suite:

```text
python3 tools/scale-test/benchmark_all.py --runs 10 --warmups 2
```

All five in-memory lanes and both database lanes passed all six scenarios, with
10 finite measured samples and 2 warm-ups per scenario. The report is written
to `tools/scale-test/benchmark_results_corrected.json`; the human output labels
in-memory `steady-state program_run_ms` separately from database
`hybrid_total_ms`.

Cold-process reports are also available with `--mode cold-process`; they launch
one fresh process per lane/scenario/sample and keep those timings in a separate
`cold_process_ms` report. `--timing-mode gc-controlled` is a separate
diagnostic path: SBCL, Python, PHP, and Node/V8 explicitly collect before the
measured path, while native C++ records GC as not applicable. Neither mode is
mixed into the default steady-state claims.

## Final verification (2026-09-13)

The final corrected run completed successfully:

    python3 tools/scale-test/benchmark_all.py --runs 10 --warmups 2

The resulting benchmark_results_corrected.json contains all seven lanes, six
scenarios, ten finite raw samples per phase, two warm-ups, the shared dataset
SHA-256/table counts, the shared reference SHA-256/scenario IDs, shaped-record
counts (137100 shaped, 1 root fallback, 5 lists) for every in-memory lane,
active PHP JIT metadata, forced-current-source C++ build metadata, and
PostgreSQL/MariaDB table-count checks. Every lane and scenario passed exact SQL,
hybrid metadata, context/parity, and result-row gates. With ten samples, p95 is
stored as null by policy.

Additional focused gates passed:

    PYTHONPATH=python uvx --from pytest pytest -q python/tests/test_unit.py
    73 passed
    node tools/check-js-optimizer.mjs       26 passed
    php tools/check-php-optimizer.php      24 passed
    cpp unit                               103/103 checks passed
    cpp SQL advanced                       3/3 checks passed

The focused cold-process and gc-controlled scenario-2 diagnostics also passed
across all five in-memory hosts. Database timings remain labelled as
persistent PDO, execute/fetch plus hybrid totals under uncontrolled
warmed-cache conditions; they are not merged into the evaluator timing table.

## Verified current behavior

### In-memory timing boundaries

All paths below were checked against the current files in `tools/scale-test`.

| Lane | Work before the timer | Current timed region | Work after the timer |
|---|---|---|---|
| Common Lisp | Dataset/context construction; `compile-source` for SQL planning; PostgreSQL and MariaDB planning; full GC | `sel:evaluate(query, context)`, which parses/compiles the source again, then `run` optimizes the AST and evaluates it | SEL-to-JSON-ready materialization and report construction |
| C++ | Dataset/context construction; source parsing through `sel::compile` | Deep `context.clone()`, then `Program::run`, which optimizes the AST and evaluates it | Result equality, SQL planning, SQL comparison, and reporting |
| JavaScript | Dataset/context construction; source parsing through `compile` | `Program.run`, including AST optimization and evaluation, followed by recursive `benchmarkValue` materialization | Result comparison, SQL planning/comparison, and reporting |
| PHP | Dataset/context construction; source parsing through `Sel::compile` | `Program::run`, including AST optimization and evaluation, followed by recursive `benchmark_value` materialization | Result comparison, SQL planning/comparison, and reporting |
| Python | Dataset/context construction; source parsing through `compile` | `Program.run`, including AST optimization and evaluation, followed by recursive `benchmark_value` materialization | Result comparison, SQL planning/comparison, and reporting |

Consequences:

- the current cells do not measure one common phase;
- Lisp uniquely reparses source inside the timer;
- C++ uniquely deep-clones the complete context inside the timer;
- JavaScript, PHP, and Python include native result materialization, while
  Lisp and C++ do not;
- AST optimization is inside `Program.run` in all five hosts, including Lisp's
  `run`; calling a program “compiled” currently means parsed and checked, not
  pre-optimized;
- SQL planning is outside every reported in-memory timer and must remain a
  separate metric if measured later.

The six checked-in queries contain no assignment operations. The runners
currently reuse one prepared context across scenarios in Lisp, JavaScript,
PHP, and Python; C++ clones it for every scenario. Reuse is acceptable only
after an untimed validation proves that every scenario leaves the prepared
context unchanged.

### Input construction and physical representation

Lisp, JavaScript, PHP, and Python construct dataset objects through their
native-to-SEL conversion paths, which create shared shaped records for the
homogeneous fixture. The C++ JSON parser constructs objects as a generic
`Value::none()` followed by repeated `set()` calls, so fixture rows do not use
C++'s shaped-record path. That is a benchmark-loader defect, not an inherent
C++ evaluator cost.

C++ setup also iterates `customers->entries()` when adding `dist_berlin`.
For a list this materializes numeric entry keys and copied `Value` handles.
This happens before the scenario timers, so it inflates process preparation
and memory rather than the currently reported scenario cells, but it should
still be replaced by direct list-slot iteration.

### Repetitions, process lifetime, JIT, and GC

`benchmark_all.py` currently starts a fresh host process for every repetition.
Each process loads the fixture once and executes all six scenarios once in
fixed order. Therefore:

- there are no discarded warm-up iterations;
- each reported sample excludes the outer host process startup and fixture
  load, but runtime/JIT/cache state differs by scenario position;
- the ten samples are ten fresh-process samples of each lane-specific timed
  region, not ten steady-state iterations in one process;
- C++ is built once before its repetitions with the Makefile default of
  `-std=c++23 -O2 -Wall -Wextra -Wpedantic`, unless the environment overrides
  `CXXFLAGS`; the report does not record the effective compiler or flags;
- PHP is launched with CLI opcache enabled, a 128 MiB JIT buffer, and
  `opcache.jit=1255`, but the runner does not verify or report effective JIT
  state;
- Node/V8 receives no explicit warm-up control or diagnostic flags;
- Lisp runs a full GC immediately before every scenario, outside its timer;
  the other hosts do not force GC;
- interpreter/compiler/runtime versions, CPU information, GC activity, and
  effective optimization settings are not emitted in the result artifact.

The wrapper's statement that timings “exclude process startup” is true for the
in-memory cells, but not for the database client processes described below.

### Wrapper argument propagation and sample validation

The wrapper accepts `--dataset` and `--reference`, but currently:

- only the Lisp temporary worktree is wired to the selected dataset;
- C++ is invoked with `DEFAULT_DATASET` and `DEFAULT_REFERENCE`;
- JavaScript, PHP, and Python receive neither selected path and use their
  defaults;
- the database harness uses its fixed `benchmark_results.json` and fixed
  10x database selection;
- the heading always says “137,100-row dataset,” even for a custom file.

The custom arguments therefore do not currently define one cross-lane run and
must not be advertised as doing so.

For JSON host reports the wrapper requires six scenario objects and checks each
scenario's `passed` flag, but it does not explicitly require the exact six IDs
once each. C++ output parsing does require six matching `PASS` lines with the
expected ID set. The database parser requires six printed parity passes, but
its missing-timing check is cumulative: after the first repetition it cannot
reliably prove that every later repetition supplied one timing for every
scenario. There is no final assertion that each lane/scenario has exactly the
requested sample count.

### Current database boundary

`run_benchmarks.py` does not maintain persistent database connections. For
every scenario it launches:

- `docker exec ... psql ... -c` for PostgreSQL; and
- a new `mariadb ... -e` client for MariaDB.

The measured interval surrounds each complete subprocess call. It includes
client process startup, connection/authentication, server execution, server
serialization, and transfer of stdout. It excludes Python parsing of stdout,
canonical comparison, and the hand-written Python hybrid continuation.

The two database boundaries are also asymmetric:

- PostgreSQL wraps the query in `SELECT json_agg(t) FROM (...) t`, so server-side
  JSON aggregation and serialization are timed;
- MariaDB executes the original SQL and emits tab-separated rows;
- PostgreSQL JSON decoding and MariaDB TSV parsing both happen after their
  timers;
- hybrid scenarios time only the SQL leg; continuation execution is outside
  the reported database timing.

Each aggregate database repetition starts a new Python harness, which in turn
starts twelve database clients. There is no explicit cold-buffer reset, no
discarded warm-up, and no claim can be made that the samples are either true
cold-cache or controlled warm-cache server measurements. `EXPLAIN`/`ANALYZE`
is not part of these timings.

### Current parity and statistics

The current suite has useful gates, but their exact meanings differ:

- each fresh Lisp result is compared with the checked-in reference for scenario
  metadata, exact generated PostgreSQL/MariaDB SQL strings, hybrid metadata,
  and in-memory rows;
- C++, JavaScript, PHP, and Python compare in-memory output and exact generated
  SQL strings with that reference, and check hybrid/pure-SQL/continuation
  metadata;
- the database harness executes SQL stored in the Lisp reference; it does not
  regenerate SQL in the database step;
- database row comparison lowercases field names, preserves row order, maps
  `NULL`/`"NULL"` to null, and rounds every numeric-looking value to four decimal
  places. Its printed “100% Match” is therefore tolerant canonical parity, not
  byte-for-byte or exact-decimal parity;
- hybrid database parity uses hand-written Python implementations for scenarios
  2 and 3 rather than executing a generated SEL continuation program.

The wrapper currently reports arithmetic mean, minimum, and maximum only. It
does not emit median, variance, standard deviation, percentiles, raw samples,
or machine-readable environment metadata. Progress is one line after a whole
six-scenario process run completes; runner output is captured, so a long
scenario is silent until that run exits.

## Target contract

### 1. Keep distinct metrics distinct

The primary in-memory comparison should use the public compiled-program
contract shared by all five hosts:

1. load and convert the fixture outside all scenario timers;
2. parse/check the source once into a `Program` outside `program_run_ms`;
3. start the timer;
4. call the compiled program's `run(prepared_context)` exactly once;
5. stop the timer;
6. materialize and verify the result outside `program_run_ms`.

Call this metric `program_run_ms`. Under the current APIs it intentionally
includes AST optimization plus evaluation in every host. The Lisp runner must
call `sel:run` on the already compiled `prog`, not `sel:evaluate` on the source.
C++ must not clone the context inside this timer.

Report these additional phases separately:

| Metric | Required boundary |
|---|---|
| `compile_ms` | Parse and compile-time checks only, matching the current public `compile` contract |
| `program_run_ms` | Current public `Program.run`: AST optimization plus evaluation against a prepared context |
| `materialize_ms` | Recursive conversion of the returned SEL value into the canonical host report structure |
| `prepared_total_ms` | One contiguous timer around `Program.run` plus materialization; excludes compile, context preparation, and verification |
| `sql_plan_ms` | Optional and separate: hybrid planning for one named dialect; never fold this into in-memory execution |
| `cold_process_ms` | External wall time for a fresh one-scenario process, including runtime/module startup, fixture load, context construction, compile, run, and materialization |

Do not construct an “end-to-end” timer that includes parity comparison. The
parity gate is mandatory, but its implementation cost is not application work.

If a future API can cache or accept an already optimized AST, add
`prepared_eval_ms` as a diagnostic kernel metric. Do not substitute it for
`program_run_ms`, and do not publish it for only some hosts.

### 2. Define context isolation uniformly

Before timing, run every scenario once in an untimed validation mode and prove
that the prepared context has the same canonical fingerprint before and after.
For the current six read-only queries, all lanes may then reuse their prepared
context.

If a future scenario mutates context, choose one policy for all hosts:

- rebuild/clone an isolated context before the timer and report
  `context_isolation_ms` separately; or
- include isolation in a separately named application metric for every host.

Never charge a deep context copy to only one lane. Retain deep cloning wherever
SEL assignment semantics require it inside evaluation; benchmark isolation is
the only copy addressed by this rule.

### 3. Use equivalent logical inputs and intended native layouts

Every runner must consume the selected fixture and produce the same table
counts and a common fixture SHA-256. Physical storage may remain idiomatic per
host, but homogeneous dataset rows must use each host's intended shaped-record
representation.

Add an untimed preparation assertion/counter that reports shaped, list, and
fallback record counts. A silent C++ fallback-record load is a failed benchmark
precondition. Setup loops should use direct list iteration and must not
materialize key/value entry views unless keys are needed.

### 4. Separate steady-state and cold-process modes

#### Steady-state mode (default comparison)

- invoke each host runner once;
- construct one context and compile all six programs once;
- for each scenario, run two discarded warm-up cycles followed by ten measured
  cycles in the same process;
- warm-up cycles must exercise both `Program.run` and materialization so JIT and
  representation caches see the measured path;
- verify every measured result outside its timers and release it before the
  next cycle;
- use the same scenario order in every host and record that order. If order is
  randomized later, use and print one shared seed;
- do not force GC before each primary sample. Record available GC counters. A
  forced-GC experiment belongs in a separate `gc-controlled` diagnostic mode.

Two warm-ups are a starting default, not proof of JIT stability. The output
must retain per-iteration samples so a reviewer can detect ramp-up. If PHP or
V8 is still changing materially after two cycles, increase warm-ups for all
JIT lanes under a documented policy, rather than silently discarding selected
samples.

#### Cold-process mode (separate report)

For each lane/scenario/sample, launch a fresh process restricted to that one
scenario and measure it externally. This includes process startup and fixture
preparation by definition. Running all six scenarios in one “cold” process is
invalid because scenarios 2–6 inherit runtime and allocator state.

Cold-process and steady-state numbers must never share an unlabeled table cell.

### 5. Verify and report the runtime configuration

Every result artifact must include:

- OS/kernel, CPU model, logical CPU count, and available memory;
- runtime/compiler name and version;
- C++ compiler, effective flags, build identity, and confirmation that the
  measured binary was rebuilt from current sources;
- SBCL version, dynamic-space size, safety/speed/debug policy if overridden,
  and GC policy;
- Node/V8 version and relevant flags;
- PHP version plus requested and effective opcache/JIT settings; fail the
  JIT-enabled mode if effective JIT is unavailable;
- Python implementation/version and interpreter flags;
- dataset path, SHA-256, per-table counts, total source rows, scenario IDs,
  repetitions, warm-ups, and timing mode.

The wrapper must propagate the resolved `--dataset` and `--reference` paths to
C++, JavaScript, PHP, and Python. Lisp should receive them explicitly rather
than through a fixed-name symlink where practical. Database selection must be
derived from an explicit `--scale`/DSN configuration and checked against the
fixture metadata. Headings must use measured metadata, not hard-coded counts.

### 6. Give database lanes a comparable client boundary

The primary database metric should use persistent connections from one client
implementation and execute the original SQL in both systems—no PostgreSQL-only
`json_agg` wrapper. Define:

| Metric | Boundary |
|---|---|
| `db_execute_fetch_ms` | Execute with bound parameters, fetch every result row over an already-open persistent connection, stop after the final row is received |
| `db_materialize_ms` | Convert fetched driver rows into the common canonical result structure |
| `continuation_ms` | Execute the generated SEL continuation for a hybrid plan |
| `hybrid_total_ms` | One contiguous timer around SQL execute/fetch, materialization needed by the continuation, and continuation execution |
| `db_connect_ms` | Separate diagnostic for client connection establishment |

Use the same connection-reuse and prepared-statement policy for both databases
and print it. If driver buffering prevents a trustworthy execute/fetch split,
report the combined `db_execute_fetch_ms` rather than inventing a server-only
number.

Server-reported `EXPLAIN ANALYZE` timings and plans are valuable diagnostics,
but they are separate executions and must not replace the parity-producing
client measurement. A true cold-buffer run requires an explicit, privileged,
opt-in cache-reset procedure; otherwise label results `uncontrolled-cache` or
`warmed-connection`, never `cold-buffer`. `EXPLAIN`, parity comparison, and
schema setup stay outside primary timing.

For hybrid scenarios, replace the hand-written Python continuation with the
actual generated continuation from the designated reference implementation.
Keep SQL-only and hybrid-total timings separate.

### 7. Strengthen parity gates

Reject a timing sample—and the aggregate benchmark—unless all applicable gates
pass:

1. The exact expected scenario ID appears once in that iteration.
2. In-memory rows match the current Lisp oracle after one documented canonical
   conversion; row order remains significant.
3. PostgreSQL and MariaDB SQL strings from every host match the current Lisp
   SQL strings exactly, along with pure/hybrid and continuation-presence flags.
4. Database result rows match the in-memory oracle after executing any real
   generated continuation.
5. Numeric database comparison uses exact decimal normalization where possible.
   Any tolerance must be field-specific and recorded; remove the blanket
   four-decimal rounding and do not label tolerant comparison “exact.”
6. Fixture SHA-256, table counts, total rows, schema version, and database scale
   agree.
7. Input representation assertions pass and no lane silently falls back from
   the intended homogeneous shaped layout.
8. Every lane/scenario/phase has exactly the configured number of measured
   samples and no non-finite or negative duration.
9. Database command/connection/query errors fail immediately; they must not be
   converted into empty result sets that could accidentally compare equal.

Parity status and timing remain separate fields. A passing parity gate cannot
make incomparable timing boundaries comparable.

### 8. Report statistics that the sample count supports

For the default ten measured steady-state samples, retain every raw sample and
report:

- sample count;
- arithmetic mean;
- median;
- minimum and maximum;
- sample standard deviation and coefficient of variation.

Do not report p95 from ten samples as if it were stable: nearest-rank p95 is
simply the maximum. Report p95 only when at least 20 measured samples are
requested, define it as nearest-rank `sorted[ceil(0.95 * n) - 1]`, and print
`n/a` otherwise. Do not discard outliers automatically; annotate reruns and
environmental interference instead.

Write a machine-readable JSON artifact containing metadata, parity details,
all raw phase samples, and summary statistics. The human table should state the
metric and mode in its title—for example, `steady-state program_run_ms`—and
must not collapse database client totals into evaluator-only cells.

Progress should be streamed at bounded granularity, at least:

```text
[lane] [scenario] warmup 1/2
[lane] [scenario] measured 4/10
[database] [scenario] execute/fetch 4/10
```

The wrapper currently captures child output, so this requires either streaming
subprocess output or a small machine-readable progress channel.

## Implementation plan

1. Define a versioned result schema for metadata, phase samples, parity, and
   statistics; make the wrapper reject unknown/missing scenario IDs and wrong
   sample counts.
2. Add `--runs`, `--warmups`, `--only`, `--dataset`, `--reference`, and timing
   mode support consistently to all five host runners. Have the wrapper invoke
   each steady-state runner once and propagate resolved paths.
3. Correct the primary host boundary: precompile programs; make Lisp use
   `sel:run prog`; remove C++ context cloning from the timed region; split
   materialization from `program_run_ms` in JavaScript, PHP, and Python; add the
   same explicit materialization phase to Lisp and C++.
4. Add the untimed context-immutability check. Reuse the context for the current
   six scenarios only after it passes; otherwise isolate every host outside the
   timer and report isolation separately.
5. Make C++ fixture parsing construct shaped records and replace setup
   `entries()` iteration with direct slots. Add representation counters to all
   lanes as benchmark precondition evidence.
6. Remove Lisp's primary-mode forced GC, add two common warm-ups, and emit
   runtime/JIT/GC/build metadata. Keep explicit JIT-enabled and JIT-disabled PHP
   runs as separately labelled configurations if both are wanted.
7. Replace the database CLI subprocesses with persistent-driver execution of
   the original SQL, separate connection/materialization/continuation phases,
   and run the actual generated hybrid continuation.
8. Tighten database error handling and parity normalization, propagate scale
   and fixture identity, and add exact per-run timing-count checks.
9. Implement median, sample standard deviation, coefficient of variation, and
   conditional nearest-rank p95; save raw samples and stream bounded progress.
10. Run focused parity first, then the corrected ten-sample steady-state suite.
    Run cold-process and memory/GC diagnostics separately; do not mix them into
    the primary comparison.

## Acceptance criteria

The corrected benchmark is ready for performance claims only when:

- the result artifact proves one dataset/reference/schema identity across all
  lanes;
- every in-memory `program_run_ms` has the same boundary—compiled public
  `Program.run`, including current AST optimization, with no context cloning or
  result materialization;
- materialization and prepared-total phases are available for every in-memory
  host;
- steady-state samples come from controlled warm-up and repetition inside one
  process per host, while cold-process results are separately labelled;
- PostgreSQL and MariaDB use the same persistent-client execute/fetch boundary
  and unmodified SQL result shape;
- all exact SQL, hybrid metadata, context immutability, representation, fixture,
  row-result, error, and sample-count gates pass;
- effective JIT/compiler/runtime settings and raw samples are present; and
- the report states mean, median, range, variability, and a sample-count-valid
  percentile policy without silently removing outliers.

Until then, preserve the existing figures only as historical
**lane-specific runner timings**. They are useful evidence that parity passed
under the old harness, but they are not evaluator-only or directly comparable
seven-lane performance measurements.
