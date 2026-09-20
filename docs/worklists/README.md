# SEL working lists

Mutable worklists live here. Reviews, benchmark reports and raw scan results in
`docs/interim/` remain historical artifacts; do not rewrite them to track progress.

| Number | Working document | State | Imported item range |
| --- | --- | --- | --- |
| WL-001 | [Runtime correctness, cleanup and shared definitions](001-runtime-cleanup-and-generation.md) | Active | SEL-0001–SEL-0040 (triage table: [001-scan-triage.md](001-scan-triage.md)) |

**Next worklist: WL-002** (`002-<short-title>.md`).
**Next issue: SEL-0041** (global sequence across all worklists).

Allocate new numbers here when creating a document or item. Never reuse or
renumber IDs, including closed/rejected/merged items. A new worklist references
existing IDs rather than assigning duplicate issues new IDs. Preserve each
item's source, status, next action, closure evidence and resolution commit/date.
Priority and display order can change independently of IDs.

WL-001 imports documented findings at revision `6568201`; this import is not a
fresh verification of every old issue and does not reopen previously fixed work.
Confirmed findings, candidates, performance investigations and safe-fallback
improvement proposals are labeled separately in the working document.
