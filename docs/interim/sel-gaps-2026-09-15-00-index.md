# SEL gaps from adversarial data-processing tests — work index

Status update 2026-09-16: F1–F4 and F6 are implemented in all five hosts, starting
in Lisp; F5's earlier safe fallback was rechecked. The default full-roster
integration gate is **ALL GREEN**. All 192 mutations were caught, including
seven requiring separate live-DB runs. Desired-behavior regressions passed
2,568 SQL executions and 1,188 exact hybrid replays; the full database oracle
also passed PostgreSQL, MariaDB, MySQL and SQLite.

The latest recheck additionally closed SQLite NOCASE/RTRIM identity and derived
direct-field type-loss paths. UNKNOWN group keys and mixed-MAP downstream
FILTER retain safe fallback. F6's explicitly unique-revision TOP 1 strategy
transfers 100 complete winner rows from 100,000 revisions. C1 remains open:
database-backed SQL fuzzing exposes a pre-existing local `E_DEPTH` versus SQL
acceptance boundary (closed 2026-09-21: worklist SEL-0034 and SEL-0041; the
database-backed fuzz lane is green). The historical verification below records
the original audit, not the current fix status; see the dated updates.

The consolidated [implementation/verification report](sel-gaps-2026-09-15-08-fix-verification.md)
records the new live regression totals and explicitly separates the remaining
C1 depth/error-policy boundary from the requested fixes.

Original status: open, reproduced at `55f4aa6c01b594ca04947b03e5a4a931a0229ae0` on
2026-09-15. Originally found at `ef44fa2aea403d04386f2d77478a7a283b44611e`.
The original documents were implementation work descriptions. Priorities
below are proposed triage priorities: P1 correctness, P2 capability/contract.

## Work items

| ID | Priority / category | Actionable issue |
|---|---|---|
| F1 | P1 / silent wrong rows | [Compose TAKE/DROP as a bounded slice](sel-gaps-2026-09-15-01-take-drop-slice-composition.md) |
| F2 | P1 / silent wrong rows and groups | [Preserve trailing spaces in MariaDB text identity](sel-gaps-2026-09-15-02-mariadb-trailing-space-identity.md) |
| F3 | P1 / silent wrong group cardinality | [Preserve SEL identity for computed numeric group keys](sel-gaps-2026-09-15-03-numeric-group-key-identity.md) |
| F4 | P1 / accepted query fails in DB | [Implement RECORD duplicate-key semantics in SQL projections](sel-gaps-2026-09-15-04-duplicate-record-projections.md) |
| F5 | P1 / accepted hybrid plan fails locally | [Make joined SQL-prefix results safe for local continuation](sel-gaps-2026-09-15-05-hybrid-join-result-shape.md) |
| F6 | P2 / performance capability | [Push down latest EAV revision per entity](sel-gaps-2026-09-15-06-eav-latest-revision-pushdown.md) |
| C1 | P2 / documented semantic boundary | [Make SQL validation/error semantics explicit](sel-gaps-2026-09-15-07-sql-validation-error-contract.md) |

F1–F5 were confirmed correctness defects. F6 is a correct but expensive fallback.
C1 captures additional observed, already documented SQL behavior; it is not a
sixth newly discovered correctness defect. Do not silently change language
semantics to make a translator test pass.

Suggested sequencing: fix F1/F2 first; handle F3 and F4 as separate semantic
corrections; make F5 conservatively safe before introducing result-shape support
needed by F6. F2 and F3 touch the same group-key renderer but concern different
identity guarantees. F4 and F5 both involve SQL row shape but have different
causes. The original F5 witness also contains F1; keep independent regressions.

## Verification scope

The complete rerun at `55f4aa6` produced the same local results, SQL, parameters,
planner classifications, database rows and errors as the original audit:

- 20 normalized/star-style/EAV scenarios; small complete fixtures plus an EAV
  scale fixture of 100,000 revisions over 100 entities.
- Local evaluation: Python, PHP, JS, C++, Lisp, JS bundle, minified JS bundle,
  and a freshly built/installed Python wheel. Python raw and optimized
  evaluation agree for these 20 cases.
- SQL: the five source implementations plus the Python wheel. The JS bundles
  expose the evaluator, not independent bundled SQL translators.
- PostgreSQL 17.10, MariaDB 11.8.8, PDO SQLite 3.46.1; Python scale measurements
  used SQLite 3.51.2. MySQL itself was **not** exercised in this audit.
- Strict mode off/on; inline SQL, native prepared statements, planner-prefix
  SQL; 1,620 live SQL executions and 720 hybrid execution outcomes including
  upstream SQL errors. Refusals were recorded rather than counted as DB runs.

Successful sibling dialects and safe fallbacks are called out in each issue.
“All hosts affected” means all five source implementations and the wheel have
the stated dialect behavior; it does not mean every dialect/mode fails.

## Reproduce and inspect evidence

From the repository root:

```sh
bash tools/adversarial/reproduce.sh
python3 tools/adversarial/verify.py
```

The first command rebuilds the required artifacts, runs disposable pinned DB
containers and removes them on exit. It requires Docker, Python, Node/npm,
make and a C++23 compiler and installs dependencies. The second only inspects
saved evidence. **Its assertions describe the existing bugs**: after fixing a
bug, update this audit checker or replace it with desired-behavior regressions.
A failing old audit assertion can mean a successful fix.

Evidence lives in [tools/adversarial](../../tools/adversarial/README.md):
[fixtures and case roster](../../tools/adversarial/probe.py),
[query sources](../../tools/adversarial/queries.sel),
[self-contained local corpus](../../tools/adversarial/local.selc),
[translations](../../tools/adversarial/translations.json),
[database results](../../tools/adversarial/database-results.json),
[Python hybrid results](../../tools/adversarial/hybrid-results.json), and
[other-host/wheel hybrid replay](../../tools/adversarial/all-host-hybrid.json).
The `i` field is a **zero-based** index into the case roster; each issue gives
its indexes. `host`, `d`, `strict`, and `mode` identify the execution variant.
Database evidence keeps both positional and associative rows so duplicate
columns cannot disappear unnoticed during decoding.

Example inspection, without a database:

```python
import json
from pathlib import Path
rows = json.loads(Path('tools/adversarial/database-results.json').read_text())
for r in rows:
    if r['i'] == 8 and r['host'] == 'python' and r['mode'] == 'params':
        print(r)
```

At handoff, `tools/adversarial/` is untracked in Git. Include the required
reproduction/evidence files when sharing or committing this work; otherwise
these relative links will not travel with the issue documents. Full reports:
[original audit](../../tools/adversarial/REPORT.md),
[latest recheck](../../tools/adversarial/RECHECK-55f4aa6.md).

## Shared integration and test checklist

1. Pin desired semantics in [conformance](../../conformance/) when necessary;
   update the relevant [SQL statement cases](../../sql/cases/23-statements.sqlt),
   [bucket cases](../../sql/cases/24-bucket.sqlt), and
   [hybrid cases](../../sql/cases/25-hybrid-plans.sqlt).
2. Verify the returned data/errors against live databases, not only emitted SQL
   strings. Extend [the statement oracle](../../sql/oracle/statements.json)
   and its fixtures/runner as needed; use the adversarial adapters for all-host
   hybrid replay. Standard entry point: [oracle-db.sh](../../tools/oracle-db.sh).
3. Keep all five hand-written translator/planner lanes aligned. Regenerate
   authored `.sqlt` cases with [gen-sql-cases.mjs](../../tools/gen-sql-cases.mjs).
   For dialect changes, edit JSON and run
   [gen-sql-map.mjs](../../tools/gen-sql-map.mjs); do not hand-edit generated maps.
4. Run focused semantic tests, then the repository's required checks
   ([check.sh](../../tools/check.sh), [contributor guidance](../../CLAUDE.md)).
   Build current bundles/wheel before claiming package parity.
5. Exercise inline/native parameters and strict off/on. Assert planner
   classification, usable continuation input, final values, multiplicity,
   explicit ordering, and error behavior. Group row order without ORDER BY is
   not the same claim as group membership or cardinality.

Line numbers in these issue documents refer to `55f4aa6`; use the named symbols
when subsequent edits move them. Proposed remedies and extra regression cases
are design guidance, not claims of already tested fixes.
