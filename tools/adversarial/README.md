# SEL adversarial audit, 2026-09-15

The findings and their interpretation are in [REPORT.md](REPORT.md).
The [recheck against 55f4aa6](RECHECK-55f4aa6.md) confirms all findings still
stand. Current JSON files contain that rerun; `baseline-ef44fa2.tar.gz` preserves
the original evidence.
This directory adds an audit harness and captured evidence; it does not change
SEL's implementations or the conformance suite.

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
