# SEL Cross-Language Code Review Remediation Plan

Status: implemented (September 2026). The contract the work produced is in
docs/SQL-TRANSLATION.md §12.1, the fixtures in `sql/cases/25-hybrid-plans.sqlt`,
and the contributor checklist in docs/EXTENDING.md; the CHANGELOG entry lists
what changed per host. The text below is the plan as written.

This plan addresses the findings from the read-only review of the JavaScript,
Python, PHP, C++, and Common Lisp implementations. Each part is intended to be
implemented and validated independently, in order, so that a partial rollout
always leaves the repository in a testable state.

## Findings covered

| ID | Finding | Main hosts | Priority |
| --- | --- | --- | --- |
| F1 | Hybrid planning unwinds the raw AST before normalization; JS also drops optimizer options | JS, C++ | P2 |
| F2 | `source_tables` returns binding names instead of physical database tables; Lisp omits planner fields | JS, Python, PHP, Lisp | P2 |
| F3 | Common Lisp optimization mutates caller-owned AST nodes | Lisp | P2 |
| F4 | Unused optimizer helpers and PHP cleanup debris | JS, Python, C++, PHP | P3 |
| F5 | Every `Program.run()` re-optimizes and copies the AST | All hosts | P3 |
| F6 | Optimizer and planner logic is duplicated without sufficient parity protection | All hosts | P2/P3 |

The existing conformance suites passing does not close these findings: most are
planner metadata, execution-path, immutability, or maintainability contracts
that are not covered by the current 801 language cases.

## Dependency and rollout order

```text
Part 0  Contract fixtures and test harness
   |
   +--> Part 1  Planner normalization and option handling
   +--> Part 2  HybridPlan/source-table contract
   +--> Part 3  Common Lisp AST immutability
   +--> Part 4  Dead-code and PHP cleanup
   |
   +--> Part 5  Safe repeated-run optimization strategy
             |
             +--> Part 6  Cross-host parity and DRY guardrails
```

Part 0 is required before behavior changes. Parts 1–4 are otherwise
independent. Part 5 deliberately follows the contract fixes because caching
must not preserve the current Lisp mutation or the current cross-host metadata
differences. Part 6 can begin with fixtures from Part 0, but its final form
should follow the behavior changes.

---

## Part 0 — Establish language-neutral planner fixtures

### Objective

Create a small, deterministic planner contract suite that catches the current
review findings without requiring a live database.

### Scope

Add cases covering:

1. Direct pushdown:

   ```sel
   ORDERS .> TAKE(1)
   ```

2. Pushdown through a constant/helper assignment:

   ```sel
   X = ORDERS; X .> TAKE(1)
   ```

3. A genuine hybrid plan with an unsupported suffix.
4. A pure-memory plan with a bound relation appearing in the source AST.
5. A relation whose binding name differs from its physical table name.
6. Case variants of a binding name, if variable names are case-insensitive by
   contract.
7. A reusable program whose AST is snapshotted before and after execution.
8. Planner options, including strict SQL mode and any optimizer controls that
   are intentionally public.

### Expected contract

Each host should report equivalent values for:

- pure SQL, hybrid, and pure-memory flags;
- whether normalization enables the helper-assignment case to push down;
- physical database source names, in first-use order and without duplicates;
- SQL prefix presence and continuation presence;
- unchanged caller-owned AST after `run` and planning;
- documented option behavior.

### Candidate implementation locations

- Shared fixture data: `sql/` or `tools/` alongside the existing SQL cases.
- JS: `tools/check-js-optimizer.mjs` or a focused hybrid-check script.
- PHP: `tools/check-php-optimizer.php` or a focused hybrid-check script.
- Python: `python/tests/test_unit.py` plus a no-database focused runner if the
  repository wants parity without requiring pytest.
- C++: `cpp/tests/sql_unit.cpp`.
- Lisp: `lisp/tests/unit.lisp`.

### Acceptance criteria

- The new cases fail on the current implementation for every known finding.
- The fixtures can run without PostgreSQL, MariaDB, or SQLite.
- The expected values are documented once rather than independently invented
  in each host test.

---

## Part 1 — Normalize before hybrid planning

### Objective

Make hybrid planning inspect the same normalized logical AST that the SQL
translator receives.

### JavaScript work

Update `js/src/sql/hybrid.mjs` so the planner:

1. Builds the constants scope from the catalog.
2. Runs SQL normalization on `program.ast`.
3. Applies logical optimization to the normalized AST.
4. Passes the planner's documented optimizer options into that optimization
   step, or narrows the option type and explicitly documents that only SQL
   translation options are accepted.
5. Uses the normalized AST consistently for unwind, fallback, and source-table
   analysis where appropriate.

Do not duplicate a second normalization algorithm; reuse the existing SQL
constants and normalizer modules used by the translator.

### C++ work

Update `cpp/sel_sql_hybrid.cpp` so the pre-unwind path uses the existing C++ SQL
normalization machinery. Keep `Options::strict` behavior unchanged and avoid
introducing a second copy of normalization logic.

### Regression coverage

The helper-assignment case must produce a pushable SQL plan in JS and C++,
matching Python, PHP, and Lisp. Add a case proving that a non-normalizable
assignment still falls back cleanly rather than being incorrectly translated.

### Acceptance criteria

- `X = ORDERS; X .> TAKE(1)` is not classified as pure memory when the
  normalized form is SQL-translatable.
- Invalid assignments retain the existing refusal/fallback behavior.
- JS planner options have one documented, tested meaning.
- JS and C++ focused suites pass without changing ordinary conformance output.

---

## Part 2 — Complete and align the `HybridPlan` contract

### Objective

Make every host expose the same planner information and make
`source_tables` mean physical database sources, not SEL binding names.

### Source-table semantics

Define `source_tables` as the physical `relation.from` value used by the SQL
plan, preserving first-use order and deduplicating by physical source. For
relation queries, preserve the existing representation of the raw query source
or document a separate source kind if that distinction is needed.

Update:

- `js/src/sql/hybrid.mjs`;
- `python/sel/sql/hybrid.py`;
- `php/src/Sql/Hybrid.php`.

The implementation should read the already validated relation binding rather
than infer a table name from the AST variable.

### Common Lisp plan shape

Extend `lisp/src/sql/hybrid.lisp` and `lisp/src/sql/package.lisp` with the
missing contract fields:

- dialect;
- continuation AST;
- source tables;
- any compatibility aliases required by the public cross-host API.

Populate these fields on every return path: pure SQL, hybrid, and pure memory.
Add a source-table traversal that follows the same physical-source semantics as
the other hosts.

### Acceptance criteria

- A binding `ORDERS -> orders` reports `orders` in every host.
- Duplicate references to the same physical source occur once.
- Lisp exposes the same information as the other hosts.
- Existing C++ expectations remain unchanged.
- The new planner fixtures pass across all five implementations.

---

## Part 3 — Restore Common Lisp AST immutability

### Objective

Ensure optimizer entry points never mutate the AST owned by a compiled
program or caller.

### Work

In `lisp/src/optimizer.lisp`, make the non-pipeline branches follow the same
copy-on-write rule already used by the JavaScript, Python, PHP, and C++
optimizers:

- shallow-copy the current node before changing child slots;
- recursively optimize copied children;
- preserve immutable/shared child data where safe;
- return the copied/folded node rather than mutating the input node.

Check both `optimize-ast-logical` and `optimize-ast-in-memory`. Do not weaken
the existing depth guard or change evaluator mutation semantics for runtime
values and contexts.

### Regression coverage

Snapshot a compiled program's AST, call `run`, logical optimization, physical
optimization, and SQL planning, then verify the original AST is structurally
unchanged. Also verify that repeated runs produce identical results.

### Acceptance criteria

- No optimizer path mutates `program-ast`.
- Existing Lisp conformance and unit tests remain green.
- AST immutability behavior matches the other four hosts.

---

## Part 4 — Remove dead code and local cleanup debris

### Objective

Reduce copied scaffolding and make unused code visible to normal language
tooling.

### Work by host

- JavaScript: remove `walkNode` and `nodeHasVar` from
  `js/src/optimizer.mjs`, unless a live caller is intentionally added.
- Python: remove `walk_node` and `node_has_var` from
  `python/sel/optimizer.py`, unless a live caller is intentionally added.
- C++: remove `opt_node_has_var` from `cpp/sel.cpp`, unless it becomes part of
  the optimizer implementation.
- PHP: remove the unused `SelError` import from `php/src/Sql/Hybrid.php` and
  collapse the duplicate documentation block in
  `php/src/Sql/Normalise.php`.

Do not remove helpers that are used only indirectly without checking their
callers; in particular, retain the relation-source helpers used by join
predicate pushdown.

### Acceptance criteria

- Repository-wide searches show no remaining unreferenced helpers identified
  in the review.
- JS syntax checks, PHP lint, Python compilation, C++ build, and Lisp tests pass.
- No behavior change is introduced by this cleanup part.

---

## Part 5 — Define and implement a safe repeated-run optimization strategy

### Objective

Avoid paying the full AST optimization/copy cost on every `Program.run()` while
preserving public AST and custom-program semantics.

### Design decisions required first

1. Decide whether public AST fields are mutable API. JavaScript, Python, PHP,
   and Lisp currently expose or return AST structures directly.
2. Decide whether a program compiled from an AST can be safely cached by
   identity, version, or explicit freeze/compile ownership.
3. Decide whether SQL logical plans and in-memory physical plans need separate
   cached forms.
4. Define thread-safety/reentrancy expectations for C++ and shared program
   objects.

### Candidate approaches

- Cache immutable physical and logical ASTs inside a private execution object,
  while leaving the public source AST untouched.
- Add an explicit `prepare()`/compiled-plan object rather than silently caching
  behind mutable public fields.
- If caching is rejected for API reasons, document that `Program.run()` is a
  compile-and-run operation and optimize only the proven hot path.

### Acceptance criteria

- Repeated-run benchmarks demonstrate the intended reduction or document why
  caching is unsafe.
- Results and context mutation remain unchanged.
- SQL translation never accidentally receives a physical-only optimized AST.
- Cache behavior is safe under the host's documented concurrency model.

This part should not be implemented before Parts 1–3, because caching the
current inconsistent or mutating behavior would make the defects harder to
remove.

---

## Part 6 — Add parity and DRY guardrails

### Objective

Reduce future drift without forcing all five implementations into an
unnatural shared runtime abstraction.

### Work

1. Make the Part 0 planner fixtures language-neutral and run them in every
   host.
2. Generate or validate shared vocabulary where practical, especially:
   `PIPELINE_OPS`, planner field aliases, and source-table contract names.
3. Keep optimizer algorithms native to each language, but require every
   cross-host optimizer rewrite to have:
   - one language-neutral semantic case;
   - one regression case for refusal/unsupported shapes where relevant;
   - one immutability check.
4. Add a contributor checklist covering normalization order, metadata meaning,
   AST ownership, option forwarding, and fallback behavior.
5. Mark generated files clearly and ensure regeneration is deterministic.

### Acceptance criteria

- A new planner or optimizer port cannot pass its local tests while silently
  omitting the shared planner contract.
- The duplicated implementation remains understandable and host-idiomatic,
  but semantic behavior is checked from shared fixtures.
- The parity suite runs without a live database.

---

## Final validation gate

Before closing the remediation series:

```text
git diff --check
node js/bin/conformance.mjs
node tools/check-js-optimizer.mjs
php php/bin/conformance
php tools/check-php-optimizer.php
PYTHONPATH=python python3 -m pytest -q python/tests/test_unit.py
bash lisp/bin/test
make -C cpp -j2 build/unit build/sqlunit build/conformance
./cpp/build/unit
./cpp/build/sqlunit
(cd cpp/.. && ./cpp/build/conformance)
```

The Python environment must provide pytest for this gate; a missing test
dependency is an infrastructure failure, not a passing result. Add the
dependency to the repository's documented development setup if it is not
already managed elsewhere.

The final report should distinguish:

- behavior fixes;
- contract/parity fixes;
- dead-code cleanup;
- measured optimization gains;
- checks unavailable because of infrastructure or database constraints.

