# Recheck against 55f4aa6

Date: 2026-09-15. Original audit: `ef44fa2aea403d04386f2d77478a7a283b44611e`.
Rechecked commit: `55f4aa6c01b594ca04947b03e5a4a931a0229ae0` (`wip, A E C2`).

**All five correctness findings and the performance limitation still stand.**
The complete reproduction script finished successfully, including its assertions
that describe the previously observed defects.

| Finding | Recheck result |
|---|---|
| F1: TAKE/DROP pagination | Still returns `[2,3]` instead of `[2]`; the empty-result case still returns ID 3. All three databases, including strict mode. |
| F2: trailing-space text identity | MariaDB still selects both rows and merges the two groups. PostgreSQL/SQLite controls remain correct. |
| F3: numeric grouping identity | PostgreSQL/MariaDB still merge the two distinct local keys. SQLite still refuses and falls back correctly. |
| F4: duplicate RECORD keys | PostgreSQL/MariaDB still reject the generated SQL. SQLite's hybrid fixture still succeeds. |
| F5: joined hybrid row shape | All five source implementations and the wheel still produce `E_NO_KEY`. PostgreSQL/MariaDB fail in both strict settings; SQLite fails with strict off and falls back safely with strict on. |
| F6: latest EAV revision | Every planner still selects pure-memory execution. The 100,000-revision fixture still needs 1,000 times as many input rows as the SQL baseline. |

## Verification

Rebuilt the C++ runner, both JS bundles, and the installed Python wheel from the
updated source. Recreated the same pinned disposable PostgreSQL/MariaDB/tooling
containers and reran all 20 scenarios:

- Eight local runtime/package variants.
- Six SQL translator variants, three databases, strict off/on, inline/native
  prepared/planner-prefix statements: **1,620 live SQL executions**.
- **720 hybrid execution outcomes**, including upstream SQL errors, across
  the five source implementations and the installed wheel.
- Python's unoptimized and optimized local evaluation also agreed on all 20 cases.

Parsed JSON comparison against the saved original evidence found **no changes**
in `local-results.json`, `translations.json`, `database-results.json`,
`hybrid-results.json`, or `all-host-hybrid.json`. This includes SQL strings,
parameters, classifications, fetched rows and recorded errors—not just the
overall failure counts.

The new commit fixes other optimizer and translator issues from the project's
earlier review. Those changes do not alter the behavior of these audit scenarios.

## Repeated scale measurement

For 100,000 EAV revisions and 100 entities, the updated Python local path took
approximately **3.82 seconds** including fetch, SEL ingestion, evaluation and
result materialization. Indexed SQL baselines returning 100 rows took about
**10.0 ms SQLite**, **20.0 ms PostgreSQL**, and **1.03 ms MariaDB**. These are
single-run observations; the stable limitation is the unchanged 1,000× input-row
amplification, not the exact timing ratio.

The current JSON evidence files contain this recheck's outputs. The original
JSON evidence and report are preserved in `baseline-ef44fa2.tar.gz`. The original
[REPORT.md](REPORT.md) retains its original commit and timing measurements.
The reproduction run removed all three disposable containers on exit. No SEL
implementation files were modified.
