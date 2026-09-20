# Structured dead-code and DRY review

Baseline: `6ca2fad` (`optimization round complete`). The worktree was clean when
this scan began; `git push origin main` returned “Everything up-to-date”. This
review adds tools/evidence/documentation, not runtime cleanup or generation.

## Scope and confidence

The scan inventories 156 runtime source files across all five lanes, including
SQL translation. Generated dialect tables are identified and excluded from
candidate detection. Vendored SRELL, bundles, dependencies and benchmark archives
are outside the runtime scan. Public declarations, tests and callers were also
searched when checking candidates manually.

[scan.json](code-scan/scan.json) records source hashes, single-reference
candidates, Python AST import candidates and exact normalized eight-line clone
windows. The 32 clone groups are triage results, not 32 bugs. Lexical reference
counts cannot prove reachability: callbacks, exports, reflection, Lisp macros,
C++ overloads/templates and external consumers need manual review. No lane gets
a “no dead code” certificate from this scan.

[registry.json](code-scan/registry.json) compares live startup builtin metadata
in all five lanes. C++ is freshly compiled from the current sources, rather than
using a potentially stale executable. The snapshot compares minimum/maximum
arity, lazy/binds flags and extra-rule presence; it does not prove callback-body
or binder-scope equivalence.

Both `node tools/gen-sql-map.mjs --check` and
`node tools/gen-sql-cases.mjs --check` pass: six dialects and 852 cases are current.
No full correctness or performance suite was rerun for this read-only review.

## Findings by lane

**Cross-lane P2: confirmed compile-time arity drift.** The fresh runtime probe
compiles `LINK(L, R, TRUE, TRUE)` and `LINK_LEFT(L, R, TRUE, TRUE)` without running
them. Python raises `E_ARITY`; JavaScript, PHP, C++ and Lisp accept both at compile
time. [spec/errors.md](../../spec/errors.md) explicitly makes `E_ARITY` a
compile-time error, and [SPEC §7.4](../../spec/SPEC.md) lists only three- and
five-argument LINK forms. This is observable validation-phase drift, not merely a different way to
store equivalent metadata. Add shared compile-phase tests and unify the allowed
counts before emitting a shared definition. No runtime-result equivalence is
claimed for those malformed programs.


### Python — partially DRY; obsolete state and small cleanup candidates

**PY-1 / P2: unused per-shape alias cache.**
[value.py:30](../../python/sel/value.py#L30) reserves `alias_cache` in slots and
[value.py:37](../../python/sel/value.py#L37) allocates an empty dictionary for every
shape. Repository searches find no consumer; current alias ownership is in the
bounded global plan cache in `builtins/structure.py`. Remove or lazily preserve
this legacy surface after checking compatibility. Do not remove the live global
cache. Measure schema-churn allocations/retention after cleanup.

**PY-2 / P3: unused internal imports and helper.** Candidate imports confirmed
unused in their module AST/source are `D` in `builtins/structure.py`, `Callable`
and `BIN` in `eval.py`, `SelError` in `optimizer.py`, `Any` in `sql/constants.py`,
and `decode_utf8`/`to_code_points` in `value.py`.
[utf8.py:122](../../python/sel/utf8.py#L122) defines `bytes_equal` as `a == b` with
no repository caller. These are cleanup opportunities, not demonstrated hot-path
costs. Check direct-module consumers before deleting an accessible helper.

**PY-3 / P3: duplicated collection iteration is partly intentional.**
`iter_entries` and `iter_elements` in [value.py:96](../../python/sel/value.py#L96)
repeat packed-shape/list traversal. The latter additionally exposes a scalar as
synthetic key `1`; they are not interchangeable. A shared traversal primitive
could reduce maintenance, but extra generator delegation may cost hot-loop time.
Retain the value-only fast path that avoids creating keys. Benchmark before
merging these loops.

Do **not** remove `__init__` re-exports, builtin imports used for registration,
Python protocol methods, or compatibility aliases merely because this lexical
scan reports one reference.

### JavaScript — partially DRY; declared compatibility constrains cleanup

**JS-1 / P2: unused per-shape Map remains in the public type surface.**
[value.mjs:23](../../js/src/value.mjs#L23) eagerly creates `aliasCache`, while
[sel.d.ts:38](../../js/src/sel.d.ts#L38) exposes it. The runtime no longer reads
this field. Removing it outright changes the declared API; deprecate it or use
a compatible lazy accessor before removing it in an appropriate release.

**JS-2 / P3: duplicate entry-to-shape construction.**
[value.mjs:134](../../js/src/value.mjs#L134) `fromEntries` and
[value.mjs:159](../../js/src/value.mjs#L159) `fromEntriesPreserveDuplicates` repeat
key/value extraction and uniqueness detection. Their duplicate fallback is
intentionally different: ordinary records overwrite; joined rows can preserve
ordered duplicates. Factor only the shared preparation if its allocation cost
is acceptable. Do not unify the fallback semantics.

`functionNames` and SQL map `reset` are exported surfaces, not proven dead code.
Optimizer helpers such as `readsRowOrKey` and `stepReadsKey` have live callers;
naive symbol counters can misclassify these references when tokenizing member
or assignment syntax.

### PHP — DRY fails at a live comparator boundary

**PHP-1 / P2: two live comparators have already drifted.**
[Core.php:203](../../php/src/Builtins/Core.php#L203) `compareValues` serves ordinary
SORT through `Core::doSort`; [Structure.php:800](../../php/src/Builtins/Structure.php#L800)
serves TOP through `Structure::doTop`. They independently implement null,
numeric, boolean, text/binary and fallback-rank ordering. The recent explicit
getter change reached Structure's boolean comparison, while Core still reads
`$a->scalar`/`$b->scalar` through magic dispatch. This is concrete maintenance
drift, not merely similar-looking code. It also means ordinary SORT timing must
not automatically be attributed to the changed TOP comparator.

Choose one canonical comparison routine with native direct calls from both
operators. Preserve sign-based comparison, null ordering, numeric coercion,
source error behavior and stable tie handling. Test SORT and TOP against one
another over mixed kinds, then benchmark each independently. Do not share the
whole algorithms: a bounded TOP selection and full sorting have different work.

**PHP-2 / P3: obsolete alias field and unreachable fallback.**
[Value.php:46](../../php/src/Value.php#L46) retains a public `aliasCache` property
while `RecordShape::alias` uses static `$aliasPlans`. This is dead internal state,
though public compatibility still matters. Unlike a JS Map/Python dict/Lisp hash
table, an empty PHP array must not be assumed to have the same allocation cost.

At [Structure.php:239](../../php/src/Builtins/Structure.php#L239), `getScalar()` is
followed by `if ($s === null && $value->decVal !== null) getScalar()` again.
`getScalar()` already formats a non-null decimal cache before returning. In this
single-threaded call path the second formatting branch cannot add an outcome.
Remove the redundant guard after confirming the unchanged getter contract.

The prior [cold numeric join-key defect](php-runtime-improvements.md) is a
separate, still-open correctness issue. This review does not mark it fixed.

### Common Lisp — partially DRY; definite unused helper and costly stale slot

**CL-1 / P2: unused hash table allocated per record shape.**
[value.lisp:34](../../lisp/src/value.lisp#L34) initializes `alias-cache` on every
shape. No source caller reads the generated `record-shape-alias-cache` accessor;
actual alias plans now live in the bounded global cache. Remove the obsolete
slot/initialization after checking internal consumers and reload behavior, then
repeat retained-heap and schema-churn probes. Slot removal can affect structure
layout and live images; this is not just deleting an unused local variable.

**CL-2 / P3: unused SQL helper with stale semantics.**
[hybrid.lisp:507](../../lisp/src/sql/hybrid.lisp#L507)
`collect-all-step-field-references` has no repository caller and is not exported.
It merges names with `string-equal`, unlike the live collector's case-sensitive
`string=` contract. Prefer removal; do not revive it as a convenient shared
helper without correcting the key semantics and tests.

Repeated AST-copy/walk forms in SQL hybrid/stage1 are candidates for a common
child traversal interface, but they operate on different node representations
and scope rules. Lisp macros can remove syntax repetition; they must not hide
binder handling, copy ownership or optimizer metadata invalidation.

### C++ — dead internal helpers; hot-path duplication needs measurement

**CPP-1 / P3: repository-unreferenced internal helpers.**
[sel.cpp:184](../../cpp/sel.cpp#L184) `cp_length`,
[sel.cpp:510](../../cpp/sel.cpp#L510) `mul_abs`, and
[sel.cpp:3692](../../cpp/sel.cpp#L3692) `for_each_collection_item` have no caller or
address-taking reference in runtime/tests/tools. They are internal to the
translation unit; `mul_abs` is explicitly `[[maybe_unused]]`, and the collection
helper is an uninstantiated template. Remove these rather than extending obsolete
string-arithmetic or traversal paths. This is a maintenance finding, not evidence
that the optimizer currently emits costly machine code for them.

The const-reference `dec_guard` overload also merits compiler-assisted overload
analysis, but this scan does **not** prove it unused. Public SQL setters such as
`set_guard`/`set_sargable` are not deletion candidates from internal counts alone.
`scale_up` and `dec_aligned` remain live; do not remove the entire string path.

**CPP-2 / P3: repeated checked native alignment.**
[sel.cpp:876](../../cpp/sel.cpp#L876) addition and
[sel.cpp:943](../../cpp/sel.cpp#L943) comparison repeat scale alignment with
`__int128` and overflow checks. A small inline helper returning success plus
aligned operands could own this rule once. Retain fallback behavior, signed
bounds and compiler inlining; validate with the decimal oracle and arithmetic
benchmarks. This native optimization should not become a cross-language
“small integer” abstraction for Python or Lisp.

## Global DRY assessment and generation plan

DRY holds for generated SQL dialect/case data, with content-based freshness
checks and committed outputs. It holds only partially elsewhere. The main risk
is duplicated **semantic facts and dispatch metadata**, not the unavoidable
existence of five implementations.

All five live registries have 77 builtin names and matching min/max/lazy/binds
fields. Python alone has extra arity callbacks for LINK and LINK_LEFT. The compile-only probes confirm different error timing: Python rejects four
arguments during compilation, while the other four lanes accept compilation.
The registry snapshot alone would not have established that behavioral difference.

| Priority | Proposed authored source | Generate | Keep native |
| --- | --- | --- | --- |
| 1 | `spec/builtins.json` | Names, min/max, allowed-count/parity constraints, lazy flags, docs/signature tables, direct registration scaffolding | Builtin bodies and custom host registration |
| 2 | Builtin overload/binding forms | Per-form argument roles, binder/body indices, dependency/planner classification tables, validation cases | Scope stacks, AST ownership and optimized traversals |
| 3 | `spec/math-ops.json` | Symbolic operations, source operator/builtin mapping, arity and position-source metadata | Opcode encodings, register storage, arithmetic execution |
| 4 | Language limit/error manifest | Normative depth/decimal/scale limits, error identifiers, boundary-case fixtures | Host limits, cache capacities, diagnostics formatting and positions |
| Later | Declarative SQL capability/category facts not already in dialect JSON | Shared special/lowered-function classifications where semantics truly coincide | Dialect SQL rendering, planner algorithms and database-specific exceptions |

The filenames above are proposals, not existing authorities. For example:

```json
{
  "LINK": {
    "arity": {"allowed": [3, 5]},
    "lazy": true,
    "binds": true,
    "implementation": "link"
  }
}
```

Do not generate a boolean `binds` flag and assume the scope problem is solved.
SORT, TOP, BUCKET and LINK have overloaded forms: some arguments introduce a
binder, some run inside it, and others run in the outer context. Today those
facts are retyped in parsers, dependency collectors, optimizers and SQL passes.
Capture forms explicitly, including syntactic disambiguation and `_K` scope.
Start with generated metadata consumed by existing native walkers, not a single
generic interpreter traversal forced on every host.

Math-operation vocabulary is also retyped in `python/sel/math_plan.py`,
`js/src/math_plan.mjs`, `php/src/MathPlan.php`, `lisp/src/math-plan.lisp` and
`cpp/sel.cpp`. Python/JS/PHP use numbered opcodes, C++ uses an enum, Lisp keywords.
Generate semantic mappings without imposing common binary opcode values or
changing representation-specific execution.

## Safe sequencing

1. Add shared regression fixtures for concrete semantic drift; agree on the
   normative arity/error phase before making every lane share a definition.
2. Remove confirmed internal leftovers; handle exported/public compatibility
   separately. Consolidate PHP ordering and test SORT/TOP parity.
3. Introduce a schema-validated builtin manifest and emit checked-in native
   tables with direct handler references. Keep imports/initialization order
   explicit; don't replace fast calls with runtime reflection or JSON parsing.
4. Replace the SQL-map generator's dependency on the JS startup registry with
   the authored builtin manifest, so one implementation is no longer the
   implicit authority for shared arities. Compare generated tables to live registries, including accepted arity sets
   and binding forms. Integrate deterministic `--check` into
   `tools/check-generated.sh`; reject missing, stale or hand-edited outputs.
5. Gate with conformance, SQL, API, differential/error-position tests and six
   scenarios/Mandelbrot. Retain independent decimal oracles: generating runtime
   behavior and its expected answers from the same implementation hides defects.
6. Extend generation to binding/math metadata only after that first slice works.
   Do not generate decimal algorithms, collection representations, GC policies,
   ownership rules, native UTF-8 implementations or cache strategies wholesale.

No cleanup above was applied as part of the scan. Confirmed findings and raw
candidates remain distinct; a later implementation should take small measured
steps rather than mechanically deleting everything the scanner flags.
