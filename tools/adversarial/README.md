# SEL adversarial harness

The scripts from the 2026-09-15 adversarial audit of the SQL layer, kept because
they still run: `regressions.sh` re-asserts the behaviour the audit asked for.
The audit's report, its recheck and the evidence it captured (the JSON results
and the baseline tarball) are in the git history; every script here regenerates
its own results, which are not committed.

## Current desired-behavior regressions

Run `bash tools/adversarial/regressions.sh` from the repository root. It requires
Docker, Python, Node (for the JS adapter), make and a C++23 compiler. It builds
the current C++ adapter, creates four uniquely named disposable containers,
installs PHP/Lisp tooling and a fresh Python wheel, and runs:

- `identity.py`: direct and derived text/numeric group keys, DISTINCT and exact
  filters; SQLite NOCASE/RTRIM schemas, PostgreSQL numeric scale, and MariaDB
  case/trailing-space identity. Five source hosts plus the installed wheel,
  strict off/on, inline and native prepared statements; unordered group output
  is compared by exact record multiplicity.
- `latest.py`: normalized and EAV latest-member results, empty and filtered
  histories, complete nested payloads, and 1,000/100,000 revision fixtures.
  It asserts 100 transferred winners for the 100,000-row fixture, rather than
  accepting merely a promising SQL string or planner classification.
- `witnesses.py`: the original F1–F5 reproduction queries plus composite grouping
  and slice controls, asserted against current local SEL in all six SQL lanes.
  Documented arithmetic/validation differences (C1) are deliberately separate.
- The full expression/row/statement oracle on PostgreSQL, MariaDB, MySQL and
  SQLite. The new identity/latest adapter matrix itself covers the first,
  second and fourth platforms; shared SQL cases also pin MySQL rendering.

Containers use pinned image digests, no published ports and no persistent
volumes. Only containers created by this run are stopped on exit. Each Python
harness creates uniquely named databases and drops them in `finally`; evidence
is retained in a new `/tmp/sel-identity-evidence.*` or `/tmp/sel-latest-evidence.*`
directory, whose path is printed.

For an already provisioned disposable environment, run either Python script
directly. Set `AUDIT_TOOLS`, `AUDIT_PG_CONTAINER`, `AUDIT_MARIA_CONTAINER`, and
`AUDIT_WHEEL` to the tools/DB container names and installed-wheel path. The tools
container must mount the repository read-only at `/work`, use that working
directory, and share PostgreSQL's network namespace with MariaDB listening on
3306. The scripts assume the disposable credentials established in
`regressions.sh`. Do not point them at production databases.

## Reproduce

From the repository root, on a machine with Docker, Python, Node/npm, make and
a C++23 compiler:

```sh
bash tools/adversarial/reproduce.sh
```

The script creates three named disposable containers, mounts the repository
read-only in the tooling container, builds the local C++ runner and JS bundles,
installs the current Python wheel in the container, runs the probes, and stops
its containers on exit. PostgreSQL and MariaDB expose no host ports and use no
persistent volumes. Their fixture tables are intentionally recreated repeatedly.
An existing container with any of these names causes the script to stop before
creating or deleting anything. Database images are pinned to this audit's digests;
tooling packages are installed from the image's Debian repositories.

The source roster is Python, JS, PHP, C++ and Lisp. Local execution additionally
covers the JS bundle, minified bundle and installed Python wheel. SQL additionally
covers the installed wheel; the JS bundles export only the evaluator, not a
separate bundled SQL implementation.

`verify.py` checks the captured evidence without needing Docker. Its assertions
describe the defects observed at the audited commit; a fix can correctly make
those assertions fail. This is an investigation harness, not a green regression
suite asserting desired behavior.

## Evidence

| File | Meaning |
|---|---|
| `probe.py` | Twenty named scenarios, typed bindings, small fixtures; also the initial Python discovery runner |
| `queries.sel` | Generated one-line query sources, in scenario order |
| `local.selc` | Same queries with complete inline fixtures for all batch runners |
| `local-results.json` | Canonical local outputs, eight runtime/package variants |
| `translations.json` | SQL, placeholder SQL, parameters, refusals, and planner classification; six translator variants |
| `database-results.json` | Actual driver column names, positional rows and associative rows, or SQL errors; inline, native prepared, and planner-prefix execution |
| `hybrid-results.json` | Python executor results using the real SQL-prefix rows |
| `all-host-hybrid.json` | JS/PHP/C++/Lisp/wheel executor replay using each variant's real SQL-prefix rows |
| `replay-*.sel` | Generated complete context and SQL result fixtures used for hybrid replay |
| `scale-results.json` | Python/SQLite latest-revision timings and checked output counts |
| `scale-db-results.json` | PostgreSQL/MariaDB indexed baseline and full-transfer measurements |

The `i` field in JSON is a zero-based index into `probe.py`'s `CASES` and
`queries.sel`. Database results additionally name `host`, `d` (dialect),
`strict`, and `mode`. `prefix` means the planner's SQL prefix; it is a complete
statement even when the entire plan is hybrid. The replay callbacks return
SEL Values created from ordinary associative driver rows. Parameterized SQL is
executed with native PDO prepares, not emulated by substituting literals.

Rows are retained positionally as well as by column name, so duplicate-column
defects cannot be hidden by associative decoding. SQL errors are recorded as
errors, not silently converted to empty results. Fixtures use strings for SEL
numbers, integers for SQL identifiers/foreign keys, and DECIMAL for measure `v`.
Scalar numeric formatting and unordered group output are not treated as
unexpected bugs by this report.
