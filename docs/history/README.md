# History

Documents that described work to be done, kept after the work was done.

They are here rather than deleted because they say **why** the code is shaped
the way it is, and rather than in `docs/` because they no longer describe what
the code *does*. Nothing in here is maintained against the current tree: read
them as a record, and believe `docs/` and the suite over them where they differ.

| | |
|---|---|
| [SQL-DELIVERY.md](SQL-DELIVERY.md) | The SEL→SQL delivery sequence, alpha criteria, what was ruled out of scope, and the decisions taken. Was `docs/SQL-TRANSLATION.md` §14–17 until 0.4.0 shipped. |
| [SQL-TESTING.md](SQL-TESTING.md) | Why the SQL layer kept shipping wrong answers: seven classes of check that could not fail, and what was built to close each. The test strategy the layer now has came out of this. |

`docs/PARSER-MIGRATION.md` was retired earlier and is in the git history rather
than here — it described a migration that left no trace in the tree, so there
was nothing for it to explain.
