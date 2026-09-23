# WL-001 — Runtime correctness, cleanup and shared definitions

**Working document 001 · Created 2026-09-20 · Import baseline `6568201`.**

This is the mutable work list for the structured scan and surrounding optimization
follow-ups. The [structured scan](../interim/structured-code-scan.md) and its raw
artifacts remain unchanged historical evidence. Tracking an item here neither
fixes it nor authorizes treating a candidate as proven dead code.

Issue IDs are permanent across worklists. Never renumber or reuse them. Keep
closed entries with their resolution commit, evidence and date. New findings get
the next global ID from the [worklist index](README.md); the next document is
`WL-002`. Move priorities/order without changing IDs. If a finding is split,
allocate new IDs and link them; if merged, retain redirects from the old IDs.

Statuses: **Open** = documented defect/cleanup; **Needs verification** = a candidate
or imported historical issue requiring current proof; **Investigate** = measured
tradeoff with an unresolved cause or acceptance decision; **Proposed** = an
unimplemented improvement; **Deferred** = a lower-priority design opportunity.
Use **In progress**, **Blocked**, **Resolved**, or **Retained intentionally** as
work proceeds. Resolution requires the item's closure evidence, not just a green
unrelated test. P2 precedes P3; these are planning priorities, not new severity
claims. Owners are unassigned until someone takes an item.

Scope: all named scan findings, its unnumbered candidates/generation work, recent
performance/correctness leftovers, and explicitly open nearby SQL boundaries.
Older general feature roadmaps are not automatically imported as bugs. SEL-0037
tracks reconciliation of older review records. Already completed optimization
work, rejected experiments and known false-positive exports stay out of the
implementation queue. Safe fallback limitations are marked Deferred, not defects.

## Index

| ID | Item | Lane | Status | Priority |
| --- | --- | --- | --- | --- |
| [SEL-0001](#sel-0001) | Cold numeric join keys depend on decimal-cache history | PHP (fix reached JS, Lisp) | Resolved | P2 |
| [SEL-0002](#sel-0002) | LINK / LINK_LEFT compile-time arity drift | All five | Resolved | P2 |
| [SEL-0003](#sel-0003) | Eleven pre-existing Python join-output disagreements | Python / cross-lane | Resolved | P2 |
| [SEL-0004](#sel-0004) | Consolidate live SORT and TOP comparators | PHP | Resolved | P2 |
| [SEL-0005](#sel-0005) | Retire unused per-shape alias dictionary | Python | Resolved | P2 |
| [SEL-0006](#sel-0006) | Retire unused per-shape alias Map compatibly | JavaScript | Resolved | P2 |
| [SEL-0007](#sel-0007) | Retire obsolete public alias array | PHP | Resolved | P3 |
| [SEL-0008](#sel-0008) | Remove unused record-shape alias hash table | Common Lisp | Resolved | P2 |
| [SEL-0009](#sel-0009) | Remove confirmed unused internal imports | Python | Resolved | P3 |
| [SEL-0010](#sel-0010) | Resolve unused bytes_equal helper | Python | Resolved | P3 |
| [SEL-0011](#sel-0011) | Remove unused SQL field-reference helper | Common Lisp | Resolved | P3 |
| [SEL-0012](#sel-0012) | Remove three unused internal helpers | C++ | Resolved | P3 |
| [SEL-0013](#sel-0013) | Determine whether const-reference dec_guard is unused | C++ | Resolved | P3 |
| [SEL-0014](#sel-0014) | Remove redundant scalar-formatting fallback | PHP | Resolved | P3 |
| [SEL-0015](#sel-0015) | Assess shared collection traversal | Python | Retained intentionally | P3 |
| [SEL-0016](#sel-0016) | Assess shared entry-to-shape preparation | JavaScript | Resolved | P3 |
| [SEL-0017](#sel-0017) | Assess common SQL AST traversal primitives | Common Lisp | Retained intentionally | P3 |
| [SEL-0018](#sel-0018) | Assess shared checked native scale alignment | C++ | Resolved | P3 |
| [SEL-0019](#sel-0019) | Triage remaining scan candidates and clone groups | All five | Resolved | P3 |
| [SEL-0020](#sel-0020) | Create an authored builtin manifest | All five | Resolved | P2 |
| [SEL-0021](#sel-0021) | Model overloaded binding forms once | All five | Resolved | P2 |
| [SEL-0022](#sel-0022) | Generate shared math-operation metadata | All five | Resolved | P3 |
| [SEL-0023](#sel-0023) | Centralize normative limits and error identifiers | All five | Resolved | P3 |
| [SEL-0024](#sel-0024) | Assess remaining SQL capability/category duplication | All five / SQL | Deferred | P3 |
| [SEL-0025](#sel-0025) | Remove JS registry as the implicit SQL-generator authority | Tooling / all five | Resolved | P2 |
| [SEL-0026](#sel-0026) | Add generation freshness and semantic gates | Tooling / all five | Resolved | P2 |
| [SEL-0027](#sel-0027) | Investigate remaining C++ allocation tradeoffs | C++ | Retained intentionally | P3 |
| [SEL-0028](#sel-0028) | Resolve or explicitly accept history-sensitive S6 latency | Common Lisp | Retained intentionally | P3 |
| [SEL-0029](#sel-0029) | Assess Lisp cold-path and retained-vector costs | Common Lisp | Retained intentionally | P3 |
| [SEL-0030](#sel-0030) | Investigate the small Python S6 slowdown | Python | Resolved | P2 |
| [SEL-0031](#sel-0031) | Assess heterogeneous Python metadata-cache churn | Python | Retained intentionally | P3 |
| [SEL-0032](#sel-0032) | Validate PHP mixed-query performance and comparator attribution | PHP | Resolved | P3 |
| [SEL-0033](#sel-0033) | Run the full five-lane integration gate after remediation | All five / validation | Resolved | P2 |
| [SEL-0034](#sel-0034) | Revalidate and resolve SQL depth/error-policy boundary C1 | All five / SQL | Resolved | P2 |
| [SEL-0035](#sel-0035) | Assess computed-projection type propagation | All five / SQL | Deferred | P3 |
| [SEL-0036](#sel-0036) | Assess broader latest-revision pushdown forms | All five / SQL | Deferred | P3 |
| [SEL-0037](#sel-0037) | Reconcile historical review closure records | Documentation / audit | Resolved | P3 |
| [SEL-0038](#sel-0038) | Check call arity once per parser | All five | Resolved | P3 |
| [SEL-0039](#sel-0039) | Share the SQL translator's repeated arms | All five / SQL | Resolved | P3 |
| [SEL-0040](#sel-0040) | Share PHP element iteration and optimizer child visits | PHP | Resolved | P3 |
| [SEL-0041](#sel-0041) | SQLite before 3.48 truncates SUBSTR lengths past 2^31 | Tooling / SQL oracle | Resolved | P2 |
| [SEL-0042](#sel-0042) | Lisp leaves join columns unqualified when fields declare no table | Common Lisp / SQL | Resolved | P2 |
| [SEL-0043](#sel-0043) | Positional index on a relation row: one refusal code and position | All five / SQL | Resolved | P2 |
| [SEL-0044](#sel-0044) | Probe physical_ast and plan_hybrid in the API-parity lane | All five / validation | Resolved | P3 |
| [SEL-0045](#sel-0045) | Sanitize the C++ SQL layer and planner | C++ / validation | Resolved | P3 |
| [SEL-0046](#sel-0046) | Compare refused-plan error positions in every runner | All five / validation | Resolved | P3 |
| [SEL-0047](#sel-0047) | Cover binding kinds and multi-line programs under plan_hybrid | All five / SQL | Resolved | P3 |
| [SEL-0048](#sel-0048) | SELECT_COLS after a sort wrapped the plan in four hosts; the SQL fuzz lane never ran the generator's SQL mode | All five / SQL | Resolved | P2 |
| [SEL-0049](#sel-0049) | Python's physical tree depends on the context's data; the other four depend on the AST alone | Python | Resolved | P2 |
| [SEL-0050](#sel-0050) | Carry Python's join pre-filter through a pushed FILTER to recover scenario 5 | Python | Resolved | P3 |
| [SEL-0051](#sel-0051) | The logical FILTER pushdown evaluates a pushed conjunct on rows an earlier conjunct would have short-circuited | All five | Resolved | P3 |
| [SEL-0052](#sel-0052) | Recover scenario 5 exactly: a run-time join pre-filter that proves side ownership and totality, in every host | All five | Resolved | P3 |
| [SEL-0053](#sel-0053) | Joined rows over records of different shapes: Lisp builds wrong rows and crashes, and the hosts disagree on missing, null and boolean fields | All five | Resolved | P1 |
| [SEL-0054](#sel-0054) | The physical join-predicate pushdown changes values: renumbered keys, lost join-key errors, relation names under explicit binders, first-row promotion | All five | Proposed | P2 |
| [SEL-0055](#sel-0055) | The scale harness's database lane expects SQL for scenario 6, which the planner keeps in memory | Validation | Proposed | P3 |
| [SEL-0056](#sel-0056) | Python's per-pair joined rows cost scenario 6 about 5% | Python | Proposed | P3 |

## Working items

<a id="sel-0001"></a>
### SEL-0001 — Cold numeric join keys depend on decimal-cache history

**Resolved 2026-09-20 · P2 · PHP (fix reached JS, Lisp) · Owner: unassigned.**
Source: [PHP follow-up](../interim/php-runtime-improvements.md) — Cold numeric join-key defect.

**Next action:** Share canonical decimal-key normalization between cached and newly parsed keys, preserving the integer-text shortcut. The saved reproducer joins zero rows before parsing and one afterward for numerically equal cached-decimal/text keys.

**Close when:** Cold/warm and repeated-run joins agree for mixed representations, scales, signs and zero; equality and join semantics match; add regression cases and repeat focused/application benchmarks.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`Structure::canonicalJoinKey` now sends the cached-decimal branch and the
parsed-text fallback through one `canonicalDecimalKey` helper; the integer-text
shortcut is kept. The saved reproducer answers `{"join_before_comparison":1,
"equality":true,"join_after_comparison":1}` (was 0/true/1).
Writing the conformance case first showed the defect is not PHP-only: JS and
Lisp keyed on the *formatted* decimal (`1.50` never met `"1.5"`, a negative zero
was its own key), so both now strip trailing fraction zeros and fold zero as C++
and Python already did; Lisp's plain-integer shortcut also dropped
`DIGIT-CHAR-P` for an ASCII range check. Regression cases:
`rel.link.numeric-key-matches-as-equality-does` (trailing zeros, negative zero,
leading zeros, literal-vs-text in both directions),
`rel.link.numeric-key-repeated-run-agrees` (same join twice around an equality),
and a cold/warm/both-orders probe over eight spellings in
`tools/check-php-runtime.php`. Evidence: 925 conformance cases on five hosts,
seeds 20260813 and 7 × 4,000 programs with zero disagreements on five hosts.
Focused benchmark not repeated: the numeric path's work is unchanged except for
the removed second formatting; application benchmarks were not rerun (scoped run).

<a id="sel-0002"></a>
### SEL-0002 — LINK / LINK_LEFT compile-time arity drift

**Resolved 2026-09-20 · P2 · All five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Cross-lane arity finding.

**Next action:** Enforce only three or five arguments at compile time in every lane. Python currently raises E_ARITY for four arguments; the other four compile them. Resolve this before generating shared metadata.

**Close when:** Shared compile-only cases reject malformed counts with matching error codes/positions in every lane; supported forms and custom registration remain valid.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
The drift was compile-time versus run-time, not the count: every lane already
said "3 or 5", but only Python said it through the registry's extra arity hook,
so `IF(TRUE, 1, LINK(1, 1, 1, 1))` answered `1` in JS, PHP, C++ and Lisp and
`E_ARITY` in Python. `LINK`/`LINK_LEFT` now declare the rule (`arityError` /
`arity_error` / `:arity-error`) in all five; the in-body checks stay as a guard.
Spec: §7.4 row and `errors.md` `E_ARITY` name the rule; `docs/EXTENDING.md`
§"Unusual arity" records the trap. Cases: eight `arity.link*` /
`arity.link-left*` cases in `11-arity.selt` (too few, four, four-in-a-never-
reached-call pinning `1:13`, too many). Evidence: 925/925 on five hosts.
Custom registration is untouched (the hook is optional, as before).

<a id="sel-0003"></a>
### SEL-0003 — Eleven pre-existing Python join-output disagreements

**Resolved 2026-09-20 · P2 · Python / cross-lane · Owner: unassigned.**
Source: [Python follow-up](../interim/python-runtime-improvements.md) — Differential validation limitation.

**Next action:** Minimize and classify the 11 disagreements from the saved 4,000-program corpus; compare alias ownership, duplicate keys and output shape with the normative contract. Baseline/candidate Python outputs were identical, so the arithmetic change did not introduce them.

**Close when:** Each disagreement has a regression fixture and documented resolution; the same seeded cross-lane corpus has no unexplained differences.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
Rerunning `SEL_IMPLS="js cpp python" tools/fuzz.sh 4000 20260813` on the import
baseline reproduced exactly the 11. All eleven are one defect: every program is
`ROWS .> LINK[_LEFT](CUSTS, …)` with a *named* right relation, and Python's
`_link` never applied `ensure_row_table_alias` to right elements, so `CUSTS`,
`custs` and `_2` held the bare element (two keys) where the other four hosts
held it extended with `CUSTS`/`custs` (four keys), as spec §7.4 says. Python
now aliases right rows in the sample, the hashed buckets and the nested loop.
Minimal fixtures: `rel.link.row-shape-named-right` and
`rel.link.row-shape-named-right-five-argument-form`.
The same look found the mirror defect in the other four hosts: an *unnamed*
right argument was extended with a `_2` key holding itself (and the unmatched
`LINK_LEFT` null row carried a `_2` field). `_1`/`_2` name a position; all five
now bind such arguments bare (`rel.link.literal-binders-are-bare`,
`rel.link-left.literal-right-null-row-is-bare`; spec §7.4 sentence added).
Evidence: seed 20260813 × 4,000 and seed 7 × 4,000 both report 0 disagreements
across five hosts, 0 crashes; 925/925 conformance on five hosts; 624 pytest.

<a id="sel-0004"></a>
### SEL-0004 — Consolidate live SORT and TOP comparators

**Resolved 2026-09-20 · P2 · PHP · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PHP-1.

**Next action:** Choose one native comparison routine for Core::doSort and Structure::doTop. Their duplicated null/numeric/boolean/text ordering has drifted: only Structure uses explicit boolean getters. Keep the sorting and bounded-selection algorithms separate.

**Close when:** SORT/TOP parity covers mixed kinds, nulls, numeric coercion, stable ties and errors; benchmark both routes independently.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`Structure::compareValues` is deleted; `Structure::doTop` orders with
`Core::compareValues`, which now reads booleans through `getScalar()` (the one
difference between the two copies — the `__get` path on the SORT side, which
SEL-0032 noted never received the getter change). The heap selection in TOP and
`usort` in SORT are untouched. Parity: the existing SORT/TOP conformance cases
(mixed kinds, NULLs, numeric coercion, stable ties, direction errors) pass
925/925; PHP optimizer check 135/135. Routes timed separately after the change
on one 20,000-element list mixing NULL, decimals, booleans and text (best of 5):
SORT 606 ms, SORT_BY 596 ms, TOP 50 13 ms, TOP_BY 50 29 ms — recorded as the
post-change reference, not as a before/after claim.

<a id="sel-0005"></a>
### SEL-0005 — Retire unused per-shape alias dictionary

**Resolved 2026-09-20 · P2 · Python · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PY-1.

**Next action:** Remove or lazily preserve RecordShape.alias_cache after checking direct-module compatibility. Preserve the bounded global alias-plan cache.

**Close when:** No eager unused dictionary per shape; compatibility decision recorded; metadata, schema-churn and retained-memory checks pass.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`RecordShape.alias_cache` had no reader or writer in `python/`, `tools/` or
`python/tests` (the live plans are `builtins/structure.py:_ALIAS_PLANS`, bounded
at 256). The slot and its eager dict are removed; `_ALIAS_PLANS` is untouched.
Compatibility: `RecordShape` is not in `sel.__all__` and is reached only from
`sel.value`; direct-module readers get `AttributeError`, recorded in the changelog.
Evidence: 624 pytest, 925 conformance, `tools/metadata/python.py` passes.

<a id="sel-0006"></a>
### SEL-0006 — Retire unused per-shape alias Map compatibly

**Resolved 2026-09-20 · P2 · JavaScript · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — JS-1.

**Next action:** Handle aliasCache as a declared public surface in sel.d.ts: deprecate, lazily preserve, or remove under an explicit compatibility policy. Do not simply delete the type/member.

**Close when:** Public API/type tests and ownership checks pass; unused per-shape Map allocation is removed or an evidence-backed compatibility exception is recorded.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
Policy chosen: deprecate and preserve lazily. `sel.d.ts` keeps
`RecordShape.aliasCache` with a `@deprecated` note (always empty; removed in
the next minor release); `value.mjs` serves it from a getter backed by a
module `WeakMap`, so a shape owns `keys`, `keyMap`, `size` and nothing else
until someone reads the member. `RecordShapeAlias` stays exported for the
declaration. Evidence: a fresh shape's own keys are exactly those three, the
getter returns one stable `Map`; 925 conformance, JS runtime (25) and
`tools/metadata/js.mjs` pass.

<a id="sel-0007"></a>
### SEL-0007 — Retire obsolete public alias array

**Resolved 2026-09-20 · P3 · PHP · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PHP-2, alias field.

**Next action:** Review RecordShape::$aliasCache compatibility and remove or deprecate the internally unused property. Keep static aliasPlans ownership. Do not assume an empty PHP array costs the same as a JS Map.

**Close when:** Public compatibility and live alias-plan behavior are verified; retained-memory impact is measured rather than assumed.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`RecordShape::$aliasCache` had no reader or writer in `php/`, `tools/` or
`examples/`; live plans are the static `$aliasPlans` (bounded, reported by
`stats()['alias_cache_entries']`, which `tools/metadata/php.php` asserts). The
property is removed. Measured, not assumed: 20,000 `new RecordShape` on PHP
8.5 went from 1078.7 to 1062.7 bytes per shape — the 16-byte property slot,
about 1.5% of a shape. Compatibility: a public property on a 0.7.x class;
a reader now sees PHP's undefined-property warning, noted in the changelog.
Evidence: 925 conformance, PHP runtime (35) and metadata checks pass.

<a id="sel-0008"></a>
### SEL-0008 — Remove unused record-shape alias hash table

**Resolved 2026-09-20 · P2 · Common Lisp · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — CL-1.

**Next action:** Retire the alias-cache slot/initialization after checking internal consumers, structure layout and image reload behavior. Keep the bounded global plan cache.

**Close when:** No unused hash-table allocation per new shape; reload/ownership checks and retained-heap/schema-churn probes pass.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
The `alias-cache` slot of `record-shape` had no reader or writer (live plans:
`*alias-plan-cache*` in `builtins/structure.lisp`, keyed by shape, bounded).
Slot and its per-shape `equal` hash table removed; `%make-record-shape` was
already positional over the three remaining slots. Image reload: no saved core
exists — every `lisp/bin/*` loads from source through ASDF's cache, which
recompiles on the edit — so no stale layout can survive. Evidence: 925
conformance, 852 SQL cases, FiveAM 532/532, `tools/metadata/lisp.lisp` pass.

<a id="sel-0009"></a>
### SEL-0009 — Remove confirmed unused internal imports

**Resolved 2026-09-20 · P3 · Python · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PY-2, imports.

**Next action:** Review D in builtins/structure.py; Callable and BIN in eval.py; SelError in optimizer.py; Any in sql/constants.py; decode_utf8 and to_code_points in value.py. Preserve registration imports, re-exports and protocol/API surfaces.

**Close when:** Only verified unused imports are removed; import/registration/API tests and the Python unit suite pass.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
Each name was grepped in its module and for re-import elsewhere before
removal: `D` (structure.py, 0 uses), `Callable` (eval.py, 0), `BIN` (eval.py,
only in a docstring), `SelError` (optimizer.py, 0), `Any` (sql/constants.py,
0), `decode_utf8` and `to_code_points` (value.py, 0). `Any` in eval.py is used
and stays; registration imports and `sel.__all__` are untouched. Evidence:
624 pytest, 925 conformance, metadata check.

<a id="sel-0010"></a>
### SEL-0010 — Resolve unused bytes_equal helper

**Resolved 2026-09-20 · P3 · Python · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PY-2, helper.

**Next action:** Confirm compatibility for direct imports of utf8.bytes_equal, which has no repository caller and only returns a == b; remove or retain with an explicit reason.

**Close when:** Repository and external-surface review recorded; helper removed safely or retained intentionally with the decision documented.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
Removed. `utf8.bytes_equal` had no caller in `python/`, `tools/` or
`python/tests`, is not in `sel.__all__`, and Python's `Value` compares BIN
scalars with `==` directly. JS and Lisp keep their `bytesEqual`/`bytes-equal`
because their value equality calls it; the file-for-file shape rule covers
concerns, not every helper. Evidence: 624 pytest, 925 conformance.

<a id="sel-0011"></a>
### SEL-0011 — Remove unused SQL field-reference helper

**Resolved 2026-09-20 · P3 · Common Lisp · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — CL-2.

**Next action:** Remove unexported collect-all-step-field-references if the no-caller result still holds. Do not reuse its string-equal merging for case-sensitive record keys.

**Close when:** References/exports rechecked; SQL and case-sensitive field-name tests pass after removal, or a justified live use is documented and corrected.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`collect-all-step-field-references` had one occurrence in the tree (its
definition), is not exported from either package, and its `string-equal`
merge was not reused. Removed. Evidence: 852 Lisp SQL cases (564 mirrored),
925 conformance, FiveAM 532/532.

<a id="sel-0012"></a>
### SEL-0012 — Remove three unused internal helpers

**Resolved 2026-09-20 · P3 · C++ · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — CPP-1, confirmed internal candidates.

**Next action:** Recheck and remove cp_length, mul_abs and for_each_collection_item. Preserve live scale_up/dec_aligned paths; do not infer machine-code savings from unused source alone.

**Close when:** Runtime/test/tool reference checks and a fresh build pass; decimal, UTF-8 and collection tests remain green.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`cp_length`, `mul_abs` (already `[[maybe_unused]]`) and the
`for_each_collection_item` template each had one occurrence across `cpp/`
(definition only; `cpp/bin`, `cpp/tests`, headers checked). All three removed;
`for_each_collection_value`, `scale_up`/`dec_aligned` and `mul_limbs` stay.
Fresh `make`: no new warnings; 171/171 unit (decimal, UTF-8, value), 925
conformance. No machine-code saving is claimed.

<a id="sel-0013"></a>
### SEL-0013 — Determine whether const-reference dec_guard is unused

**Resolved 2026-09-20 · P3 · C++ · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — CPP-1, overload candidate.

**Next action:** Use compiler-aware overload/call analysis; lexical counts cannot distinguish this overload from the live rvalue guard.

**Close when:** Prove it removable and test the removal, or document its live call sites and close as retained. Do not delete it from the scan alone.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
Compiler-assisted, as asked: the `const Dec&` overload was deleted and the
translation unit rebuilt. A call passing an lvalue could not bind to the
remaining `Dec&&` overload and would have failed to compile; the build is
clean, so all 18 call sites pass temporaries (`dec_from_mantissa(...)`,
`dec_make(...)`, `dec_from_limbs(...)`). No other TU names `dec_guard`. The
overload was `[[maybe_unused]]`, which is why lexical counts could not tell.
Evidence: 171/171 unit, 925 conformance, decimal oracle 199,942 cases 0
mismatches.

<a id="sel-0014"></a>
### SEL-0014 — Remove redundant scalar-formatting fallback

**Resolved 2026-09-20 · P3 · PHP · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PHP-2, unreachable branch.

**Next action:** Check and remove the second getScalar call guarded by null scalar plus non-null decVal: the preceding getter already formats that decimal cache.

**Close when:** Getter contract, lazy formatting, public scalar writes and join-key tests prove behavior unchanged.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`Value::getScalar()` formats a cached decimal on its first call and returns
the stored scalar otherwise, so after one call `$s === null` implies
`decVal === null` and the guarded second call could never produce a different
value. The branch is removed with a one-line note of the contract. Quick
check: `15-relational.selt` 86/86, PHP runtime 35 checks (including the
cold/warm join-key probe from SEL-0001), and a text-keyed (`$==`) join over a
`Value::num` left side still pairs. Full gate follows this batch.

<a id="sel-0015"></a>
### SEL-0015 — Assess shared collection traversal

**Retained intentionally 2026-09-20 · P3 · Python · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — PY-3.

**Next action:** Evaluate factoring duplicated packed-list/shape traversal in iter_entries and iter_elements. Preserve the scalar-as-key-1 distinction and the value-only path that avoids synthetic keys.

**Close when:** A measured extraction preserves semantics without an unacceptable hot-loop cost, or retain the duplication with benchmark evidence and rationale.

**Resolution:** Retained intentionally 2026-09-20.
The only semantic difference between `iter_entries` and `iter_elements` is the
final scalar-as-key-`1` yield, so the shared form is `iter_elements` =
scalar check + `yield from iter_entries(value)`. Measured (best of 7,
`timeit`): a 200,000-element packed list traversed 5× went from 245.0 ms to
256.7 ms (+4.8%), a 200-key shaped record traversed 3,000× from 65.9 ms to
73.7 ms (+11.9%) — generator delegation adds a frame hop per item on exactly
the loops the evaluator's aggregates (`aggregate.py`, 2 call sites) run.
`iter_values` already covers the value-only path without synthetic keys. Two
14-line functions whose bodies are byte-identical apart from the tail are the
cheaper maintenance cost; revisit only if a CPython release makes
`yield from` free.

<a id="sel-0016"></a>
### SEL-0016 — Assess shared entry-to-shape preparation

**Resolved 2026-09-20 · P3 · JavaScript · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — JS-2.

**Next action:** Consider sharing key/value extraction and uniqueness detection between fromEntries and fromEntriesPreserveDuplicates; keep overwrite versus ordered-duplicate fallback behavior separate.

**Close when:** Record and join duplicate-key tests pass; allocation/latency measurements justify extraction or documented retention.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`shapedFromUniqueEntries(entries)` now owns the key/value split and the
uniqueness scan for both `fromEntries` and `fromEntriesPreserveDuplicates`,
returning the packed record or `null` on the first duplicate key; each caller
keeps its own fallback (overwrite versus ordered duplicates), which is the
difference the scan said not to unify. No extra allocation: the two arrays
were built before, and the helper returns the `Value` itself. Measured (best
of 5, same loop inline vs behind a call returning early): 8 unique keys
−10.4%, 8 keys with a duplicate −0.7%, 64 unique keys +0.2% — the boundary is
free. Evidence: 925 conformance (record and join duplicate-key cases), JS
runtime 25, optimizer 135, metadata check.

<a id="sel-0017"></a>
### SEL-0017 — Assess common SQL AST traversal primitives

**Retained intentionally 2026-09-20 · P3 · Common Lisp · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Unnumbered Lisp traversal observation.

**Next action:** Review repeated hybrid/stage1 copy/walk forms. Distinct node representations, binders, copy ownership and optimizer metadata invalidation must stay explicit.

**Close when:** Document intentional differences; any shared primitive has scope/copy/SQL regressions and measured cost, otherwise retain duplication with a reason.

**Resolution:** Retained intentionally 2026-09-20, differences documented here.
Reviewed `hybrid.lisp` `inline-literals` against `stage1.lisp`
`substitute-node`, and the walkers `source-tables`, `read-names`,
`collect-field-references`, `contains-unsupported-sql-p`, `reads-whole-row-p`.
The two copiers look alike but encode different contracts: stage 1 *refuses*
`:assign`/`:seq` (E_SQL_ASSIGN), handles the `:clist` representation that only
exists after stage 1, and counts depth against the evaluator's limit
(E_SQL_DEPTH); `inline-literals` runs before stage 1 on the evaluator's node
kinds, copies `:assign`/`:seq` through, and re-stamps an inlined literal with
the read's position. The binder-scoping arms are the same by design (a binder
shadows a same-named helper) and both say so. A shared copier would need a
per-kind refusal/copy policy argument, which is the duplication moved, not
removed, and would hide which pass owns the copy. The walkers already share
one primitive, `walk-node-children` (three users); `source-tables` keeps its
own recursion because it stops descending at a bound relation name. Planner
time, not a hot path: no measurement changes the answer.

<a id="sel-0018"></a>
### SEL-0018 — Assess shared checked native scale alignment

**Resolved 2026-09-20 · P3 · C++ · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — CPP-2.

**Next action:** Prototype a small inline helper for duplicated __int128 alignment in addition and comparison. Preserve overflow/fallback behavior; do not impose a small-int abstraction on other lanes.

**Close when:** Decimal oracle and signed-boundary cases pass; code generation and benchmarks support extraction, or record why native duplication should remain.

**Resolution:** Resolved 2026-09-20, working tree on `6568201` (commit pending).
`align_small(a, b, sa, sb, target_scale)` (inline, C++ only) owns the rule:
larger scale, gap within the 39-entry `POW10_128` table, both multiplies
checked with `__builtin_mul_overflow`; `dec_add` and `dec_cmp` call it and fall
through to the limb path on `false` exactly as their copies did. Signed
bounds and the overflow builtins are unchanged. Evidence: decimal oracle
199,942 cases 0 mismatches, 171/171 unit (signed boundaries), 925 conformance;
Mandelbrot (`tools/commit-benchmark/mandelbrot.cpp`, 2 warm-ups + 7 runs,
before/after binaries interleaved twice): medians 146.7/147.7 ms and
148.2/146.9 ms, identical outputs — within noise, as an inlined helper should
be. Not offered to Python or Lisp.

<a id="sel-0019"></a>
### SEL-0019 — Triage remaining scan candidates and clone groups

**Resolved 2026-09-20 · P3 · All five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Raw scan inventory.

**Next action:** Review the remaining 32 clone-window groups and single-reference/import candidates against the named items above. Include SQL translator branches, parser/error boilerplate and dependency walkers; distinguish live public APIs and intentional specialization.

**Close when:** Every raw candidate/group has a disposition or a new stable item ID. Do not count a clone as a bug or delete exports/reflection targets from lexical evidence.

**Resolution:** Resolved 2026-09-20 — [triage table](001-scan-triage.md).
All 32 clone windows, 25 single-reference declarations and 25 import
candidates have a disposition, each checked against the current tree rather
than the scan's counts. Tally: 13 windows resolved by SEL-0004/0015/0016/
0017/0018 or covered by SEL-0021; 7 retained with a reason (type boundaries,
fast paths); 12 tracked as SEL-0038 (parser arity check twice per host, all
five), SEL-0039 (translator: sargable-prefilter arm in all five, RECORD
projection loop in three, entry-point reset in three, Lisp collation check)
and SEL-0040 (PHP `forEachElement` in two classes, optimizer child visits).
Every single-reference declaration is either already removed (SEL-0010/0011/
0012) or a live surface: exported API mirrored across hosts, a caller in
tools/examples/generated case data, or a Python protocol method; the two C++
`Fragment` setters are public header members with no in-tree caller, noted
for an API-breaking release only. Of the imports, seven were SEL-0009 and the
other eighteen are registration imports or `__all__` re-exports. No code
changed under this item.

<a id="sel-0020"></a>
### SEL-0020 — Create an authored builtin manifest

**Resolved 2026-09-21 · P2 · All five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Generation priority 1.

**Next action:** After SEL-0002, define a validated source for names, arities/allowed counts/parity, lazy flags and direct native registration scaffolding. Keep builtin bodies and custom host registration native.

**Close when:** All 77 builtins reconcile with approved semantics; deterministic native outputs and signature docs are generated without runtime JSON/reflection overhead.

**Resolution:** Resolved 2026-09-21, working tree on `6568201` (commit pending).
Authored source `spec/builtins.json` (format `spec/builtins.md`): 77 entries
with min/max, `allowed`/`parity` extra rules with their message, lazy/binds,
the spec forms and section. `tools/gen-builtins.mjs` validates it (sorted
names, ranges, rule/message pairing, binds⇒lazy, unknown keys) and renders
six deterministic outputs: a native table per host (`_builtin_manifest.mjs`,
`_builtin_manifest.py`, `BuiltinManifest.php`, `sel_builtin_manifest.hpp`,
`builtin-manifest.lisp`) and `docs/BUILTINS.md`; `--check` is wired into
`tools/check-generated.sh`. Reconciliation is native and at startup: each
host's `define` looks the name up in its table, refuses a min/max/lazy/binds
that disagrees or a hand-written rule where the manifest owns one, installs
the manifest's rule, and after the shipped modules load refuses a manifest
name never defined — so all 77 reconcile every time any host starts (all
five lanes green), and a negative probe shows `COND` with min 2 and `LINK`
with its own rule refused, a custom name passing, and coverage naming the
missing builtins. The hand-written rules for COND, RECORD, LINK, LINK_LEFT
are deleted from all five hosts (one body each, from the manifest). Seeding
the manifest surfaced `DEDUPE`, defined in every host and tested but absent
from `spec/SPEC.md` §7.4; a row was added as the relational spelling of
`DISTINCT`. Custom registration (`register`, examples/fn-*) is untouched: a
name outside the manifest passes through. The SQL-map generator still borrows
the JS registry for arities; SEL-0025 moves it to the manifest.

<a id="sel-0021"></a>
### SEL-0021 — Model overloaded binding forms once

**Resolved 2026-09-21 · P2 · All five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Generation priority 2.

**Next action:** Build on SEL-0020 with per-form argument roles, binder/body indices, syntactic disambiguation and _K scope for SORT/TOP/BUCKET/LINK. A single binds boolean is insufficient.

**Close when:** Generated classifications agree across dependency, optimizer and SQL consumers; scope/error-position cases cover every overload and outer-context argument.

**Resolution:** Resolved 2026-09-21, working tree on `6568201` (commit pending).
The 14 binding builtins now declare `forms` in `spec/builtins.json` (format in
`spec/builtins.md`): per accepted count, in the evaluator's order, each
argument's role (`source`/`outer` evaluated where the call is, `binder` a bare
name, `body`/`key`/`proj`/`pred` inside), a `when` guard where two forms share
a count (a text literal in the direction slot before a bare name in the
binder slot — the evaluator's order, which no walker had), and the implicit
names bound inside (`_`, `_K`, and `_1`/`_2` for LINK). The generator
validates them (every accepted count has a form, guards only where needed,
the last form for a count unguarded) and renders them into the five host
tables and a "Binding forms" table in `docs/BUILTINS.md`. Each host has one
classifier — `bindingForm` (JS), `binding_form` (Python, C++ inline in
`sel_ast.hpp` so both translation units share it), `Registry::bindingForm`,
`BINDING-FORM` — with a generic two-form fallback for a host's own binding
function. Consumers switched to it: the five dependency walkers (the
SORT/TOP/BUCKET/LINK arms they each retyped are gone) and the five SQL stage-1
substitutions (which had treated every non-first argument as inner and LINK's
binder names as reads).
Found on the way: `--deps` disagreed across hosts on 9 of 13 probed forms
(TOP's binder and limit, LINK's binders, TOP_BY's direction were reads in
JS/PHP/C++/Lisp; Python alone had per-form logic), and all five hid `K` in
`SORT_BY(L, K, "DESC")`, which the evaluator reads as the key. After the
change all 15 probed forms agree on all five hosts and match the evaluator.
Pinned: six `program.deps.forms.*` API probes (60 probes agree across the
seven roster entries) and nine `agg.forms.*` conformance cases covering every
overload's outer-context argument and both guard orders (positions included).
Optimizer: its rewrites are per-builtin by design (join pushdown, sort/take
fusion) and do not classify forms; the JS/PHP optimizer checks (135 each) and
852 SQL cases per host are unchanged, which is the agreement the closure asks
for. Evidence: 925 conformance and 852 SQL cases on all five hosts, 624
pytest, 171 C++ unit, 532 FiveAM.

<a id="sel-0022"></a>
### SEL-0022 — Generate shared math-operation metadata

**Resolved 2026-09-21 · P3 · All five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Generation priority 3.

**Next action:** Define symbolic operations, operator/builtin mappings, arities and position sources. Preserve lane-native opcode numbers/enums/keywords, register storage and arithmetic execution.

**Close when:** Each native compiler/executor agrees with the manifest; math-plan/AST parity, decimal oracle and benchmarks pass without forcing common binary encodings.

**Resolution:** Resolved 2026-09-21, working tree on `6568201` (commit pending).
`spec/math-ops.json` (format `spec/math-ops.md`) names the 15 operations the
math plans compile — five binary operators, the `NEG` prefix, nine builtins —
each with its source token, operand count (1, 2 or a left fold) and the
auxiliary error-position argument (`ROUND`'s scale, `POWER`'s exponent).
`tools/gen-math-ops.mjs` validates it, cross-checks every builtin's arity
against `spec/builtins.json` (the two authored sources cannot disagree), and
renders a vocabulary table per host plus `docs/MATH-OPS.md`; `--check` is in
`tools/check-generated.sh`. Each host's compiler now classifies source nodes
through its table and maps the symbolic name to its own opcode — Python and
JS keep their numbered `OpCode`, PHP its `MathOpCode` constants, C++ its
`MathOp` enum, Lisp its keywords — and refuses to load if the manifest names
an operation its executor lacks. The three hand-written builtin arms per
host (unary five, `ROUND`/`POWER`, `MIN`/`MAX`) are one table-driven arm; the
executors, scratchpad, loads and copy-propagation rules are untouched, and
no common binary encoding was introduced.
Evidence: 934 conformance on all five hosts (plan/AST parity is what the
suite exercises, every arithmetic case running through the plan), decimal
oracle 199,942 cases with 0 mismatches on all five, JS/PHP optimizer 135
each, `ROUND(1.5, "x")` reports its error at the scale's position (1:12) on
all five. Performance: Mandelbrot on all five hosts is measured against the
`6568201` baseline in the batch's closing benchmark (see SEL-0023's entry
and the changelog).

<a id="sel-0023"></a>
### SEL-0023 — Centralize normative limits and error identifiers

**Resolved 2026-09-21 · P3 · All five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Generation priority 4.

**Next action:** Migrate normative limit/error facts to one authored authority and generate constants/catalogues/boundary fixtures. Avoid creating a second authority beside the spec; keep host/cache budgets separate.

**Close when:** Generated facts and normative docs agree; independent boundary/error-position tests remain effective and host-specific policies are preserved.

**Resolution:** Resolved 2026-09-21, working tree on `6568201` (commit pending).
`spec/limits.json` (format `spec/limits.md`) restates the four normative
numbers (`MAX_DEPTH` 200, `MAX_INT_DIGITS` and `MAX_FRAC_DIGITS` 1 000 000,
`DIV_SCALE` 10) and the 25 language error codes with their phase. It is not a
second authority: `tools/gen-limits.mjs` refuses to render unless
`spec/SPEC.md` still states each number in its own words ("at least 200)",
the §6.4 cap rows, "`DIV_SCALE` is **10**") and `spec/errors.md` lists each
code under the phase claimed — so the spec leads and the manifest can only
follow. Rendered into constants per host and `docs/LIMITS.md`; every host's
`MAX_DEPTH`, decimal caps and division scale are now defined from its
rendering instead of a literal. New gate `tools/check-error-codes.sh` (in
`tools/check.sh` after the generated-artifacts step) reads every host's
sources and requires the codes it raises to be exactly the catalogue plus the
SQL layer's `sql/errors.md` codes: all five raise exactly the 33. Kept apart,
as asked: host budgets (shape/alias caches, pools, SQL dialect limits) are not
language facts and are not here; `conformance/10-limits.selt` and the API
probes keep their literal 200s as independent oracles rather than reading
the manifest they check — the fixtures were not regenerated, on purpose.
Evidence: `10-limits.selt` 39/39 and 934 conformance on all five hosts, 624
pytest, 171 C++ unit, 532 FiveAM, generated-artifacts and error-code gates
green.

<a id="sel-0024"></a>
### SEL-0024 — Assess remaining SQL capability/category duplication

**Deferred · P3 · All five / SQL · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Later generation candidate.

**Next action:** Identify genuinely shared special/lowered-function classifications not already covered by dialect JSON. Keep rendering, planner algorithms and dialect exceptions native.

**Close when:** A semantics-backed inventory selects facts worth generating, with refusal/capability tests; intentionally different classifications are explicitly excluded.

**Resolution:** Pending.

<a id="sel-0025"></a>
### SEL-0025 — Remove JS registry as the implicit SQL-generator authority

**Resolved 2026-09-21 · P2 · Tooling / all five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Safe sequencing step 4.

**Next action:** After SEL-0020, validate SQL-map arities against the authored manifest rather than the JS startup registry. Retain runtime parity checks across all lanes.

**Close when:** SQL generation no longer depends on JS implementation metadata as authority; existing generated maps/cases remain current and all signatures are checked.

**Resolution:** Resolved 2026-09-21, working tree on `061358b` (commit pending).
`tools/gen-sql-map.mjs` no longer imports the JS host: the names and arities
an entry's `arity` is checked against come from `spec/builtins.json`, the
manifest every host's table is held to at startup, and the check now also
refuses a range that includes a count SEL rejects (`allowed`/`parity`), not
only one outside min..max. `spec/builtins.json` is a declared source of the
map's freshness group in `tools/check-generated.sh`; `sql/MAP.md` §7 rule 4
names the authority. The ten renderings are byte-identical (`--check`
current, C++ and Python tables agree on 780 records), so no host consumer
changed. Runtime parity checks (per-host `map_replay`, `sqlt` on all five,
the SQL fuzz and the Docker oracle) are retained and were rerun.

<a id="sel-0026"></a>
### SEL-0026 — Add generation freshness and semantic gates

**Resolved 2026-09-21 · P2 · Tooling / all five · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Generation safeguards.

**Next action:** With SEL-0020–0025, add deterministic emit/check support and wire all new outputs into check-generated.sh. Check accepted counts/binding forms, not just table presence. Keep independent oracles independent.

**Close when:** Missing/stale/hand-edited outputs fail; conformance, SQL, API, differential/error-position and relevant performance gates pass without correlated generated expected answers.

**Resolution:** Resolved 2026-09-21, working tree on `061358b` (commit pending).
Freshness: every rendering of the three manifests (SEL-0020/0022/0023) and
the SQL map/cases is in `tools/check-generated.sh` with content-authoritative
`--check` and an mtime fallback; a hand-edited `_limits.mjs` was shown to
fail it ("is stale") and pass again once restored. Semantics, the part table
presence cannot give: new `tools/check-manifest.sh` (gate step "manifest
semantics") predicts from `spec/builtins.json` alone and observes every host
— 381 accepted-count probes (every builtin at every count around its range,
every parity/allowed edge, through the batch runner: compiles or `E_ARITY`
at the call) and 45 binding-form probes (every form with a distinct name per
slot, a grouped name where an earlier bare-name guard would capture it, and
a guard-defeating variant per guarded form, through `--deps`). All seven
roster entries agree with the manifest; a doctored copy (LINK accepting 4,
COND any count) produces three disagreements, so the gate bites. Independence
kept: no host output feeds the predictions, `10-limits.selt` and the API
probes keep literal limits, the decimal oracle is Python's `decimal`, and
conformance/SQL expectations stay hand-written. Also in this batch: the
error-code gate (SEL-0023). Building the semantic gate surfaced a language
fact worth knowing: a plain variable in a binder slot is a bare name, so
`SORT_BY(L, K, X)` binds `K`; only a text literal in the direction slot, or
a grouped `(K)`, reaches the key form — all five hosts and the manifest
agree, and the probes now pin it.

<a id="sel-0027"></a>
### SEL-0027 — Investigate remaining C++ allocation tradeoffs

**Retained intentionally 2026-09-21 · P3 · C++ · Owner: unassigned.**
Source: [C++ follow-up](../interim/cpp-collection-improvements.md) — Recommendation and remaining work.

**Next action:** Profile S4/scalar destruction and remaining query-throughput costs before another allocator change. Do not assume manual dispatch or bypassed freelists are causal; retain the rejected-pool evidence.

**Close when:** Representative profiling explains the costs and yields a measured fix or explicit accepted tradeoff, including six scenarios, Mandelbrot and retained memory.

**Resolution:** Retained intentionally 2026-09-21; no allocator change.
Current tree, idle box: six scenarios (7 runs) S1 559 · S2 5.4 · S3 173 · S4
8.5 · S5 515 · S6 260 ms medians; S4/S6 isolated (15 runs) 8.7 / 254 ms. A
gprof build of the scale harness over 20 S4 runs answers the profiling
question the follow-up asked: the scalar-destruction path (`Value::destroy`,
22.4 M calls, 23% of samples) and `clone_at` (130 k calls, 17%) are the
harness's per-run context clone and teardown of the 137,100-row dataset, and
the largest entry (`structural_hash`, 32%) is its untimed result validation;
the timed S4 query is 8.5 ms of a 3.4 s profile. Nothing in the query's own
path stands out, so another pool or dispatch change would optimise the
harness, not the language. Mandelbrot 145–148 ms and retained memory are as
recorded in the closing benchmark of SEL-0023. Accepted; revisit only with a
profile of the timed region alone.

<a id="sel-0028"></a>
### SEL-0028 — Resolve or explicitly accept history-sensitive S6 latency

**Retained intentionally 2026-09-21 · P3 · Common Lisp · Owner: unassigned.**
Source: [Lisp follow-up](../interim/lisp-runtime-improvements.md) — S6, GC and validation placement.

**Next action:** Measure representative query sequences/context lifetimes with normal GC, keeping standard full-batch and batch-validation diagnostics separate. Reduced allocation does not erase the roughly 17% full-batch mean slowdown.

**Close when:** Causal evidence and an accepted latency policy or fix are documented; preserve original results and repeat both relevant histories.

**Resolution:** Retained intentionally 2026-09-21; no change.
Both histories repeated on the current tree with normal GC. Isolated S6, 15
runs, twice: 184.0 / 187.3 and 189.0 / 190.1 ms (median / mean) — the
recorded baseline was 185.0 / 198.6 and the recorded final 190.0 / 191.2, so
the tree sits inside that band. Full six-scenario batch (10 runs): S6 198.0 /
219.4 ms with min 176 and max 281 — the recorded final full batch was 190.5 /
217.8. The roughly 17% full-batch mean gap is therefore reproducible and is
GC placement: the batch mean carries one or two collection-bearing samples
(max 281 ms) that the isolated runs do not, while medians agree within 5%.
Policy adopted: the S6 latency figure is the isolated median under normal
GC; batch means are reported beside it and never subtracted. Original
results are preserved unchanged in the follow-up report.

<a id="sel-0029"></a>
### SEL-0029 — Assess Lisp cold-path and retained-vector costs

**Retained intentionally 2026-09-21 · P3 · Common Lisp · Owner: unassigned.**
Source: [Lisp follow-up](../interim/lisp-runtime-improvements.md) — Cold paths and retained heap.

**Next action:** Track changing-schema/alias misses, compile-and-run and long-lived compiled-program retention. Relate alias-slot cleanup SEL-0008 to measured memory; do not assume it removes the shared-vector retention cost.

**Close when:** Workload-specific costs are measured and fixed or explicitly accepted, with bounded ownership and per-invocation value isolation retained.

**Resolution:** Retained intentionally 2026-09-21; measured after SEL-0008.
Cold-path probes (`tools/lisp-runtime/benchmark.lisp`, seven samples): a
changing alias name is 1.6 µs / 928 B consed (recorded final 1.8 µs / 1,073 B,
baseline 1.6 µs / 969 B) and a changing source shape 2.8 µs / 1,943–1,950 B
(recorded final 3.2 µs / 2,231 B, baseline 2.8 µs / 1,820 B): removing the
per-shape alias table returned both cold paths to baseline latency and
removed a third of the extra allocation the follow-up had noted. Stable and
alternating aliases stay at 0.20 / 0.27 µs and 144 B; argument wrappers,
node construction, the 1,000-row projection and compile-and-run are as
recorded. Retained heap (`program-memory.lisp`): 5,000 compiled programs
10,601,312 B and after first execution 18,181,280 B (recorded 10,605,152 /
18,185,184): the roughly 112 B per executed program for its argument pair and
vector remains and is accepted — it is bounded per program, retains no
contexts or invocation values, and buys the argument-wrapper savings above.

<a id="sel-0030"></a>
### SEL-0030 — Investigate the small Python S6 slowdown

**Resolved 2026-09-21 · P2 (raised from P3: a measured regression) · Python · Owner: unassigned.**
Source: [Python follow-up](../interim/python-runtime-improvements.md) — S4/S6 follow-up.

**Next action:** Retain the approximately 1.4% isolated median / 0.6% mean cost as unresolved. S4 reverses direction in isolation; neither prepared query calls the changed sub/div functions.

**Close when:** Repeat representative histories and establish a cause, noise bound or accepted tradeoff; no unsupported attribution to arithmetic changes.

**Resolution:** Blocked on a decision, 2026-09-21. The small slowdown is
gone; a large one took its place, and its cause is established.
Isolated S4/S6 (15 runs, 3 warmups, `PYTHONHASHSEED=0`), current tree, twice:
S4 62.6 / 61.7 ms (recorded 63.9 → 63.0), S6 **1,264.8 / 1,267.9 ms** median
(recorded 718.2 → 728.1). The `6568201` baseline in a separate worktree on the
same box, same protocol: S4 62.9, S6 711.7 ms — so the regression is this
session's, not the machine's (PHP's numbers reproduce its report to the
millisecond). Bisect: the baseline tree with only the current
`python/sel/builtins/structure.py` measures S6 1,279.6 ms, and that file's
only S6-path change is SEL-0003's right-side aliasing — the spec-required
extension of each right element with its relation name, which the other four
hosts always performed. Mechanism (`tools/python-runtime/gc_s6.py`, 10 runs each): with
the cyclic collector disabled, baseline 629 → current 798 ms (+27%, the
92,002 aliased rows' own cost: one `Value` and one list each); with it
enabled, 716 → 1,252 ms (+75%), generation-2 collections during the ten runs
4 → 8 and generation-1 77 → 151. The added container objects trip full
collections, and each full collection walks the resident 137,100-row dataset;
the samples are bimodal (min 905, median 1,265) accordingly. cProfile
confirms no hot function beyond `_from_shape` (182 k calls, half of them the
alias). The earlier 1.4% S6 note and the arithmetic changes are unrelated —
S4 is flat.
Not changed, because no low-risk fix exists: (A) accept the cost as the price
of spec conformance, as JS/PHP/C++/Lisp already pay it (document it); (B) a
shared-storage alias representation in `Value` (moderate risk, touches the
hot record path); (C) leave the runtime alone and document collector policy
for large resident datasets (`gc.freeze()` after loading, or raised
thresholds), which the scale harness's `gc-controlled` mode already models;
(D) alias lazily where a static check proves the key expression and the
downstream steps never read the alias keys (moderate risk, new analysis).
The owner chose to try the collector pause, after a targeted profile.
**Profile (collector wall time through `gc.callbacks`, current tree before
the change):** S6's join step alone carried 478 ms of collector time (one
full pass, fifteen generation-1 passes, 166 generation-0); the later steps
added none. The shape is general: S1 spent 790 ms (28%) and S3 457 ms (33%,
with no full pass at all — the young-generation passes over freshly built
rows are expensive too), S5 76 ms (10%), S2/S4 nothing. Two grains were
prototyped by wrapping at runtime: a pause inside `LINK` only (S6 999, S1
2,154, S3 1,087 ms) and a pause around the whole `Program.run` (S6 785, S1
2,015, S3 926, S5 625 ms; S2, S4 and Mandelbrot flat). Safety measured
before choosing: with the collector paused for a run, `gc.collect()` found 0
unreachable objects after every scenario and Mandelbrot — SEL values are
trees, reference counting frees a run's garbage, the collector only ever
found nothing.
**Change:** `python/sel/_gc.py` (`bulk_allocation`, re-entrant, exception-
safe, no-op when the application already disabled the collector, hands it
back as found) and `Program.run` in `python/sel/__init__.py` wraps
evaluation in it. Nothing in the join changed; the aliasing's own 170 ms
stays, as it does in the other four hosts. Six tests in
`python/tests/test_gc_pause.py`: restored after a run, left off when the
application turned it off, restored when the program raises, restored once
across a nested run from a host builtin, zero cyclic garbage from evaluation
with a reused context, and the one known cycle source bounded — the
optimiser's math-plan compiler leaves about ten closure objects per plan
when a fresh context rebuilds the physical tree, reclaimed at the first
pass after the run. `docs/EXTENDING.md` §Python records the rule a new
builtin must keep (no per-element cycles) and the application-level
`gc.freeze()` complement.
**After (same protocol, idle box):** S6 isolated 786.3 / 796.1 ms median
(from 1,264.8 / 1,267.9; the collector-off floor was 798), min–max 773–819
(from 905–1,345); full six-scenario batch S1 1,974 (from 2,860), S2 36.8
(38.1), S3 911 (1,447), S4 62.4 (62.5), S5 624 (717), S6 775 (1,385) ms, every
sample within 3% of its median, all results verified. Generation-1 and
generation-2 collections during ten S6 runs: 0. Below the pre-SEL-0003
figure of 712 ms only in the collector-off sense; the honest statement is
that the tree is now faster than the `6568201` baseline on every join-heavy
scenario while paying the aliasing the spec requires.

<a id="sel-0031"></a>
### SEL-0031 — Assess heterogeneous Python metadata-cache churn

**Retained intentionally 2026-09-21 · P3 · Python · Owner: unassigned.**
Source: [Python follow-up](../interim/python-runtime-improvements.md) — Bounded metadata under heterogeneous input.

**Next action:** Keep the 65-power/257-shape and weighted-budget probes alongside application benchmarks. Decide whether representative churn warrants a policy change; bounded retention alone does not guarantee throughput.

**Close when:** Document an accepted policy or measured improvement across stable and heterogeneous workloads without unbounded retention or an unmeasured per-hit LRU penalty.

**Resolution:** Retained intentionally 2026-09-21; policy unchanged.
`tools/python-runtime/metadata.py` on the current tree reproduces the
recorded profile: stable powers 0.16 µs per lookup, the 65-power cycle
0.84 µs (every call a miss, bounded at 10 entries), the weighted cycle of
eight exponents near 160,000 **14.08 ms** per lookup (recorded 14.34 —
recomputing a 160,000-digit power is the cost, not the cache), hot power
among 300 unique 2.9 µs, stable shapes 0.78 µs, the 257-shape cycle 2.2 µs,
hot shape among 1,000 unique 1.5 µs. Bounds hold after every operation.
Accepted policy: clear-all at the entry budget, no per-hit LRU bookkeeping
(its cost on the stable workloads that dominate the application suite was
never justified), and for applications with many distinct large scales the
documented remedy stands — cancel shared scales before exponentiation. The
probes stay beside the application suite as the regression fence.

<a id="sel-0032"></a>
### SEL-0032 — Validate PHP mixed-query performance and comparator attribution

**Resolved 2026-09-21 · P3 · PHP · Owner: unassigned.**
Source: [PHP follow-up](../interim/php-runtime-improvements.md) — Application results; also scan PHP-1.

**Next action:** Keep original S4/S6 workload-order sensitivity distinct from local getter savings. Account for the scan showing Core SORT did not receive the Structure TOP getter change; attribute measured gains only to exercised paths.

**Close when:** A current interpretation distinguishes SORT/TOP and mixed/isolated histories under recorded GMP/JIT settings; any new claim has path-specific evidence.

**Resolution:** Resolved 2026-09-21; no change.
Recorded GMP/JIT settings (`php -n`, ctype+gmp, OPcache JIT 1255, 128 M
buffer), current tree. Fixed-order six-scenario batch (7 runs): S4 57.2 ms,
S6 1,086 ms — the recorded batch was 58–59 / 1,090–1,096. Isolated S4/S6
(15 runs), twice: S4 44.2 / 44.4 ms, S6 877 / 889 ms — recorded 43 / 887. The
workload-history effect is therefore reproducible to the millisecond and is
history, not code: the same tree answers S4 23% and S6 19% faster when the
five earlier scenarios' heap and JIT state are absent. Both figures are
kept, labelled by history, and neither is "the" S4 or S6 number.
Comparator attribution, focused probes against the `6568201` worktree in
alternating order: `sort.booleans` 1,929 / 1,939 → 1,453 / 1,396 µs (−26%
in both orders) — SEL-0004 gave the SORT route the explicit-getter comparator
the scan had found only in TOP, which is the path-specific evidence the item
asked for; `join.numeric_cached` 1,317 / 1,420 → 1,294 / 1,307 and
`join.literal_lazy` 1,245 / 1,269 → 1,180 / 1,135 slightly faster;
`join.numeric_text` 1,842 / 1,647 → 1,802 / 1,816, inside that probe's own
swing (its recorded baseline pair differed by 11%; its keys are parsed once
by the untimed parity run and cached, so it measures the cached path).

<a id="sel-0033"></a>
### SEL-0033 — Run the full five-lane integration gate after remediation

**Resolved 2026-09-21 · P2 · All five / validation · Owner: unassigned.**
Source: [Structured scan, retained as history](../interim/structured-code-scan.md) — Safe sequencing step 5; follow-up validation limits.

**Next action:** Schedule the complete repository gate for a coherent cleanup/generation checkpoint. Recent optimization follow-ups ran targeted suites, not the full five-lane gate; this is a validation gap, not evidence of a failing gate.

**Close when:** Record exact revision, commands and complete results, plus relevant sanitizer/DB/performance checks; failures receive their own IDs and are not hidden by narrower checks.

**Resolution:** Pending. Progress 2026-09-20: `tools/check.sh` ran ALL GREEN on
the uncommitted working tree carrying SEL-0001–0012 (roster js, js-bundle,
js-bundle-min, php, cpp, lisp, python; 47 layers), and the database layers
ran against pinned Docker servers (`tools/oracle-db.sh run …` with a PHP
client image carrying pdo_mysql/pdo_pgsql): oracle 0 differ on mariadb,
mysql, postgresql, sqlite; mutations 192 caught, 0 survived, 0 skipped.
Decision recorded 2026-09-20: database-backed checks run against Docker
servers, never skipped. Repeated later the same day on the tree carrying
SEL-0001–0018: gate ALL GREEN (47 layers), oracle 0 differ on all four
dialects, mutations 192 caught / 0 skipped. 2026-09-21, tree carrying
SEL-0001–0023: gate ALL GREEN (48 layers), database layers green, and a
baseline-versus-current benchmark on all five hosts (Mandelbrot, startup,
conformance wall time; table in the changelog) with no regression. Not yet
the closure then: no committed revision to pin, and `make asan` was not run.

Closure 2026-09-21, on committed revision `371f090` ("wip optimizations and
cleanup", `git status` clean; carries SEL-0001–0032 and the SEL-0030 collector
pause), C++ rebuilt and the JS bundle regenerated first. Commands and results:

- `tools/check.sh` — ALL GREEN on js, js-bundle, js-bundle-min, php, cpp,
  lisp, python; 48 layers (earlier progress notes counted the runner's own
  banner and said 49); wall time 1,108 s. Among them: 934 conformance cases
  per host, 852 SQL cases per host, error codes, manifest semantics,
  generated artifacts, host API parity, documentation examples, decimal
  versus Python's oracle, differential fuzz 4,000 programs seed 20260813
  (0 disagreements, 0 host crashes) and SQL fuzz 2,000 programs seed 20260905.
- `cd cpp && make asan` — unit 171/171 and conformance 934/934 under the
  address, leak and undefined-behaviour sanitizers, no report.
- `tools/oracle-db.sh run bash -c 'tools/check-sql-oracle.sh; tools/mutate-sql.sh'`
  against pinned Docker servers (mariadb 11.8, mysql 8.4, postgres 17, plus
  sqlite) — oracle expressions/rows/statements 0 differing on every dialect
  (mariadb 377 agree, mysql 379, postgresql 387, sqlite 317; the
  "refused" counts are declared non-translations); mutations 192 caught,
  0 survived, 0 skipped. The six DSN-backed mutations that the gate's own
  mutation layer reports as skipped without a DSN are among the 192.
- Benchmark, `6568201` baseline worktree against `371f090`, interleaved,
  idle box: every host within run-to-run spread (C++ Mandelbrot 148.7 / 148.6
  → 153.2 / 146.4 ms; JS 66.2 / 65.0 → 65.0 / 65.4; Python 357.8 / 350.1 →
  354.7 / 352.1; PHP 483.0 / 483.6 → 490.3 / 493.2; Lisp 95.0 / 81.0 → 79.0 /
  78.0; startup and conformance wall time flat, the current tree running 934
  cases to the baseline's 925). Table in the changelog.

No failure surfaced, so no new IDs were opened by this closure. Later
checkpoints repeat the same four commands; the recipe for the database lane
is in `tools/oracle-db.sh`.

<a id="sel-0034"></a>
### SEL-0034 — Revalidate and resolve SQL depth/error-policy boundary C1

**Resolved 2026-09-21 · P2 · All five / SQL · Owner: unassigned.**
Source: [Earlier gap verification](../interim/sel-gaps-2026-09-15-08-fix-verification.md) — Historical C1, explicitly still open.

**Next action:** Reproduce the older database-fuzz cases where local SEL raises E_DEPTH while SQL normalization accepts the expression. The earlier report explicitly leaves C1 open; it has not been rerun during this backlog import.

**Close when:** Current reproduction and a normative error/translation policy are recorded; targeted database tests and local error-phase/position checks pass after resolution.

**Resolution:** Resolved 2026-09-21; reproduced on `371f090`, fixed in all five hosts the same day (decision: charge the wrappers).

The boundary is still there, and it is now exactly characterised. Since the
report, `E_SQL_DEPTH` was added to stage 1 and to the render walk in every
host (`sql/cases/18-host-neutrality.sqlt`, the three `neutral.depth.*`
cases), so a flat chain agrees: 200 terms translate and evaluate, 201 is
`E_DEPTH` here and `E_SQL_DEPTH` there. What the translator still does not
charge is what stage 1 removes before either guard looks: **the top-level
sequence (one level) and the assignment that defines a substituted name (one
more)**. spec/SPEC.md §6.4 charges both, and `conformance/10-limits.selt`
pins it (`lim.eval-depth-through-an-assignment`: 199 behind `X =` is
`E_DEPTH` at 1:5). A boundary corpus through every host's `batch` and
`sqlfuzz` runners, all five identical on both lanes:

| Program | Evaluator | Translator |
|---|---|---|
| 200-term chain, alone | `200` | translated |
| 201-term chain, alone | `E_DEPTH` | `E_SQL_DEPTH` |
| `A = ""; <200-term chain>` | `E_DEPTH` | translated |
| `X = <199-term chain>; X` | `E_DEPTH` at 1:5 | translated |
| `X = <200-term chain>; X` | `E_DEPTH` | translated |
| `X = <201-term chain>; X` | `E_DEPTH` | `E_SQL_DEPTH` |
| `A = 1; IF(FALSE, 7, <199-term chain>)` | `E_DEPTH` | translated |
| `A = 1; (<199-term chain>) * 2` | `E_DEPTH` | translated |

So the translator is short by exactly the levels the wrappers cost: one
program of width for a bare sequence, two behind an assignment. The
database-backed lane the report cites, `tools/oracle-db.sh run
tools/fuzz-sql.sh 2000 20260905`, reproduces the report's figure to the
program: 5 differing programs on each of mariadb, mysql, postgresql, the ANSI
probe and sqlite, all of the shape above (chains of 199 or 200 terms behind a
sequence, an assignment, an `IF` branch or a parenthesis), every one reported
as "SEL rejects this with E_DEPTH and the translation succeeded". The lane
exits 1; it is not green and was not claimed to be. The host-versus-host half
of the same lane reports 0 disagreements on every dialect, as the gate does.
The five hosts also agree on the opposite direction: `Y = <150>; X = Y +
<60>; X` evaluates to 210 and is refused, because the constant check runs on
the substituted subtree (`E_SQL_INVALID` citing `E_DEPTH`) — a conservative
refusal §11.4 permits, recorded here so nobody mistakes it for the defect.

Normative policy, applied: **stage 1 substitutes a definition at the depth
the evaluator evaluated it** — the `;` sequence costs one level and each
assignment one more (spec §6.4), so the substitution walk in every host
(`normalise.py`, `normalise.mjs`, `Normalise.php`, `sel_sql_stage1.cpp`,
`stage1.lisp`) starts from that depth rather than from zero: a start-depth
argument threaded through `record`, five lines of logic in all. A flat chain
is untouched; refusals are added only for programs the evaluator already
rejects; `sql/errors.md` now states the rule beside its contract sentence.
After the change the boundary corpus agrees row for row on all five hosts,
`E_SQL_DEPTH` landing at the same position as `E_DEPTH` (1:5 for the
assignment case, 1:21 for the `IF` branch, 1:9 for the parenthesis).

Pinned by four cases in `sql/cases/18-host-neutrality.sqlt`
(`neutral.depth.the-sequence-costs-a-level` and
`neutral.depth.an-assignment-costs-another`, each with a just-under twin) and
five mutations, one per host (`*-stage-one-wrappers-cost-nothing`, the start
depth reset to zero), all caught by that host's `sqlt` run; the catalogue is
197 entries. Validation on the tree: 856 SQL cases per host, `tools/check.sh`
ALL GREEN (48 layers, 1,082 s; SQL fuzz host-versus-host 0 disagreements),
and against pinned Docker servers through the rebuilt client of SEL-0041:
oracle 0 differing on every dialect, mutations 197 caught / 0 survived / 0
skipped, and the lane this item was opened for — `tools/oracle-db.sh run
tools/fuzz-sql.sh 2000 20260905` — green for the first time: 0 differing on
mariadb, mysql, postgresql, sqlite and the ANSI probe, with the 744 programs
that do not evaluate all 744 refused by the translator (739 before). No
performance dimension: the change is one integer per program. The conservative
refusal of `Y = <150>; X = Y + <60>; X` stands as recorded above.

<a id="sel-0035"></a>
### SEL-0035 — Assess computed-projection type propagation

**Deferred · P3 · All five / SQL · Owner: unassigned.**
Source: [Earlier gap verification](../interim/sel-gaps-2026-09-15-08-fix-verification.md) — Explicit remaining boundary.

**Next action:** Consider broader type propagation through computed projections, including regrouping a projected COUNT. Existing grouped-prefix/local-continuation fallback is safe; this is an optimization opportunity, not a confirmed wrong-result bug.

**Close when:** Either retain the boundary deliberately or add exact hybrid/SQL parity cases and prove expanded pushdown sound across dialects.

**Resolution:** Pending.

<a id="sel-0036"></a>
### SEL-0036 — Assess broader latest-revision pushdown forms

**Deferred · P3 · All five / SQL · Owner: unassigned.**
Source: [Earlier gap verification](../interim/sel-gaps-2026-09-15-08-fix-verification.md) — F6 safe-fallback boundaries.

**Next action:** Decide whether to extend beyond the uniqueness/NOT NULL-backed TOP 1 strategy: raw/correlated inputs, composite partitions, computed keys, TOP 0/N>1, extra member aggregates, named binders and incompatible ordering currently retain fallback.

**Close when:** Chosen extensions have explicit preconditions and live exact replay coverage; unsupported forms continue to fall back safely. Do not reopen already verified F6 work.

**Resolution:** Pending.

<a id="sel-0037"></a>
### SEL-0037 — Reconcile historical review closure records

**Resolved 2026-09-22 · P3 · Documentation / audit · Owner: unassigned.**
Source: [Earlier review index](../interim/review-2026-09-15-00-index.md) — Older review inventory.

**Next action:** Cross-reference earlier review entries against later fixes, changelog and verification artifacts. Preserve historic verdicts; do not assume every old CONFIRMED report remains open or that an implemented plan closes unrelated findings.

**Close when:** Each historical finding is linked to closure evidence, an existing SEL ID, or a newly allocated ID after current verification; closed F1–F6 gap work is not duplicated.

**Resolution:** Resolved 2026-09-22. The 33 numbered findings of the
2026-09-15 review each carry a `Status: FIXED` line naming its changelog
section; none was reopened. The two lists that never went to verification —
13 low-severity reports and 14 critic gaps — are reconciled in a dated
section at the end of `docs/interim/review-2026-09-15-00-index.md`, each
row closed with evidence in the tree, retained with a recorded reason, or
carried by a new ID. Verified by reproduction where the tree could answer:
the Lisp column-qualification report is live (SEL-0042), the positional-index
refusal differs in code across hosts and in position for C++ (SEL-0043); the
`isSameField`, call_once-comment and BUCKET-in-spec reports are closed by the
current code and spec. Four small alignments were made in the same pass, each
covered by an existing suite: the Python unit lane fails rather than skips
without pytest (`SEL_SKIP_PYTHON_UNIT=1` opts out), the C++ optimiser leaves
assignment targets as written like the other four, Lisp's `execute-hybrid`
copies the caller's context as the other four do, and
`bucket.group-by-is-not-a-function` pins the retired verb. Three interim
documents that still called the database fuzz lane open carry dated pointers
to SEL-0034 (2026-09-21). Also in this pass, at the user's request: the four
`-Wswitch`/`-Wmissing-field-initializers` warnings in `sel_sql_translator.cpp`
are fixed, so every C++ translation unit compiles clean under the Makefile's
flags. Opened: SEL-0042–0047. The earlier F1–F6 gap work is referenced, not
repeated. Closing validation, shared with SEL-0039 and SEL-0040: gate ALL
GREEN (48 layers, 560 s, Docker servers started by the gate, native PHP
client), mutations 197 / 0 / 0, oracle and database fuzz 0 differing on every
dialect, differential fuzz 0 disagreements, no compiler warning; five-host
benchmark against `6568201` within spread (table in the changelog).

<a id="sel-0038"></a>
### SEL-0038 — Check call arity once per parser

**Resolved 2026-09-21 · P3 · All five · Owner: unassigned.**
Source: [SEL-0019 triage](001-scan-triage.md) — clone windows #2, #13, #21 (and the same two arms in the JS and C++ parsers).

**Next action:** Each parser checks min/max and the extra arity rule in two places, the plain call and the `.>` pipeline call. Move the check into one helper per host taking the spec, the count and the name token, so the compile-time E_ARITY rule (SEL-0002) has one body per lane.

**Close when:** `11-arity.selt` (including the never-reached-call cases) passes on all five; positions unchanged; no host keeps a second copy.

**Resolution:** Resolved 2026-09-21, delivered as analysed the same day
(analysis kept below). The clone is wider than the arity check:
in every host the plain call (`parse_call`) and the `.>` call share, line for
line, the registry lookup with `E_UNKNOWN_FUNC`, the min/max check, the
`arity_error` rule, and the construction of the `call` node with its record
shape — about 12 lines twice per host, 60 in all. Only the pipeline's
placeholder/left-insertion step, which sits between the lookup and the check,
differs, so the lookup cannot join the helper but everything after it can.
Recommended shape: one `finish_call(name_tok, spec, args)` per host (Python
`_finish_call`, JS `finishCall`, PHP `self::finishCall`, C++ a `Parser`
member, Lisp `finish-call`) that runs both checks against `len(args)` and
returns the node; both sites become a one-line tail call. Positions are
unchanged because every `fail` already reports `name_tok`. Pinned today by
`11-arity.selt` (126 cases, plain path, including `link.four-is-compile-time`
and `link-left.four-is-compile-time`) and by only two `.>` cases
(`pipe.arity.too-few` / `too-many` in `14-pipeline.selt`), and
`tools/check-manifest.mjs` probes the plain path alone; the recommendation
adds the `.>` form to its 381 count probes (`1 .> NAME(1, …)` with one fewer
literal) so a helper regression on the pipeline path is caught per builtin,
not per two hand-written cases. Risk low: no semantics move, and the helper
is exactly the text both sites already contain.

Delivered: `_finish_call` in `python/sel/parser.py`, `finishCall` in
`js/src/parser.mjs` and `php/src/Parser.php`, `Parser::finish_call` in
`cpp/sel.cpp`, `finish-call` in `lisp/src/parser.lisp`; both call forms in
every host end in the one call, and each parser has one `E_ARITY` body left
(99 lines removed, 117 added, the helpers and their comments included).
`tools/check-manifest.mjs` now probes the `.>` form for every count of one or
more (`1 .> NAME(1, …)`, name token at 1:6): 693 count probes per host, from
381. Validation: `11-arity.selt` and `14-pipeline.selt` 158/158 on all five
hosts, positions unchanged; `tools/check-manifest.sh` 7 hosts agree;
`tools/check.sh` ALL GREEN (48 layers, 1,140 s); against pinned Docker servers
oracle 0 differing on every dialect, mutations 197 caught / 0 survived / 0
skipped. No performance dimension: the helper is the same code at a call
boundary, once per parsed call.

<a id="sel-0039"></a>
### SEL-0039 — Share the SQL translator's repeated arms

**Resolved 2026-09-22 · P3 · All five / SQL · Owner: unassigned.**
Source: [SEL-0019 triage](001-scan-triage.md) — windows #3, #4, #6, #7, #16–18, #23, #25, #30–32.

**Next action:** Four internal repeats, to take one at a time and in every host that has it: (a) the sargable-prefilter arm, written once for `sargable $== literal` and once mirrored, in all five translators; (b) the `RECORD(k, v, …)` → projection-list loop in the grouped-MAP and plain-MAP arms (JS, PHP, C++; check Python and Lisp); (c) the per-translation state reset shared by the expression and statement entry points (Python, PHP, C++; check JS and Lisp); (d) the Lisp collation-name check in `binding-column` and `binding-raw`. Keep the *walk* per host; only the arm bodies are shared.

**Close when:** `sql/cases` (852) and the mutation catalogue pass unchanged on every host, the semantic oracle still agrees, and each repeat has one body per lane or a recorded reason.

**Resolution:** Resolved 2026-09-22; all four repeats have one body per lane.
(a) The sargable-prefilter arm's two conditions (`sargable $== literal` and
the mirror) share one body in all five translators. (b) `record_fields` /
`recordFields` / `record-fields` gives the `(name, value)` pairs of a RECORD
call with the evaluator's two refusals, and the bucket projection, the bucket
key and the MAP projection read it — fifteen hand-written loops gone. (c) One
prologue per host (`_begin`, `begin`, `Translator::begin`,
`call-with-translation`) does the dialect and alias checks, the state reset,
the constant scope, stage 1 and the planner's look; the two entry points
differ only in what they do with the plan. Found while merging: JS's
`translate` did not reset the subquery counter where `translateStatement`
did — latent, since every public entry builds a fresh translator, and gone.
Lisp keeps `*subquery-counter*` dynamically bound around the continuation,
which is why its helper takes a function. (d) `check-collation` in
`binding.lisp` returns `(values exact sargable)` for both constructors, as
`_check_collation` does in Python. Every mutation anchor still matches (197).
Validation: 856 SQL cases on all five, SQL fuzz host-versus-host 0
disagreements on every dialect, Lisp unit 532, JS and PHP hybrid checks 135
each, pytest 630; the full gate and database lanes in the closing run below.

<a id="sel-0040"></a>
### SEL-0040 — Share PHP element iteration and optimizer child visits

**Resolved 2026-09-22 · P3 · PHP · Owner: unassigned.**
Source: [SEL-0019 triage](001-scan-triage.md) — windows #10, #12.

**Next action:** (a) `Core::forEachElement` and `Structure::forEachElement` are the same function as two private statics; keep one (on `Value`, or one public static) and point 13 call sites at it. (b) `Optimizer::fieldRefs` and `Optimizer::readsVar` repeat the child-visit loops; one `forEachChild($node, $visit)` keyed by the node-shape table would give the optimizer one place that knows which keys hold children.

**Close when:** PHP conformance, optimizer (135) and runtime checks pass; the aggregate/relational hot paths measure no worse (`tools/benchmark-php-runtime.php`).

**Resolution:** Resolved 2026-09-22. (a) `Value::forEachElement` is the one
body, walking each storage layout directly as before; the two private statics
in `Builtins/Core.php` and `Builtins/Structure.php` are gone and their eleven
call sites read `$value->forEachElement(...)`. (b) `Optimizer::forEachChild`
visits a node's children through two constants, `CHILD_LISTS` and
`CHILD_NODES`, the one place the optimizer's walks know the node shapes;
`fieldRefs` and `readsVar` call it. Validation: PHP conformance 935, optimizer
135, runtime 35, SQL cases 856. Performance: `tools/benchmark-php-runtime.php`
measures scalars and decimals, not aggregates, and is within ±7% noise on
sub-microsecond operations; the relational scale scenarios
(`tools/scale-test/sel_benchmarks.php`, 7 runs, best of two rounds, against
a worktree of `302a38e`) are the measure that exercises the change: S1 3,485
→ 3,516 ms, S2 36.5 → 37.1, S3 1,109 → 1,120, S4 75.2 → 75.6, S5 2,250 →
2,241, S6 1,224 → 1,234 — within 1.6% everywhere, no direction.

<a id="sel-0041"></a>
### SEL-0041 — SQLite before 3.48 truncates SUBSTR lengths past 2^31

**Resolved 2026-09-21 · P2 · Tooling / SQL oracle · Owner: unassigned.**
Source: SEL-0034 revalidation, 2026-09-21 — the database-backed SQL fuzz lane's two non-depth differences, both on sqlite.

**Next action:** Decide how the layer treats SQLite builds that mishandle a `substr` length at or past 2^31. Measured: `substr('Zażółć', 2, 4294967299)` is `ażó` and `substr('a👍b', 2, 9223372036854775807)` is `a` on SQLite 3.46.1 (the `php:cli` oracle image's library, Debian trixie); both are correct on 3.51.2 (this box's Python and native PHP). SEL answers `ażółć` and `👍b`; the fuzz lane reports the two as differing and nothing declares a caveat. MariaDB and MySQL answer correctly; PostgreSQL refuses the `CAST(… AS INTEGER)` as out of range, which the oracle accepts as a server refusal. Options: (a) clamp the length in the sqlite template (`spec`: a length past the text's end already means "to the end", so `min({2}, 2147483647)` is semantically identical for a non-negative length — but `min(NULL, …)` and negative lengths need the same care the existing guards take, and every sqlite `SUBSTR` case's expected SQL changes); (b) pin the oracle's SQLite version — rebuild `sel-php-oracle:local` on a base whose libsqlite3 is 3.47 or later and record the floor in `sql/oracle/README.md`, leaving consumers on older SQLite with a documented boundary; (c) both.

**Close when:** The policy is recorded; the sqlite fuzz lane reports the two programs as agreeing (or as a declared, named caveat); a `.sqlt` case pins whatever the template emits; `tools/check-sql-oracle.sh` stays 0 differing.

**Resolution:** Resolved 2026-09-21, option (b): the oracle's SQLite is pinned
by a floor and the floor is documented; the map is unchanged, so no `.sqlt`
case changes. Measured to find the floor: 3.45.3 and 3.46.1 wrong, 3.48.0,
3.49.2, 3.51.2 and 3.53.4 right (3.47 not measured; the floor is the first
version measured correct). `tools/oracle-php-client.Dockerfile` is the
client the repository's own database runs use — PHP CLI on Alpine with
`pdo_mysql`, `pdo_pgsql` and `pdo_sqlite`, libsqlite3 3.53.4 today — and
`tools/oracle-db.sh` asks the PHP client (not a host `sqlite3` binary, which
is not what the oracle runs) for its library version and refuses below
`SQLITE_FLOOR=3.48.0`, printing the version in its readiness line.
`sql/oracle/README.md` records the floor, the reason and the shim recipe, and
says that a consumer on an older SQLite gets the truncation: the library is
what is wrong, not the translation. With the rebuilt client the fuzz lane
reports the two programs as agreeing (sqlite 276 agree, 0 differ) and the
oracle stays 0 differing on all four dialects.

<a id="sel-0042"></a>
### SEL-0042 — Lisp leaves join columns unqualified when fields declare no table

**Resolved 2026-09-22 · P2 · Common Lisp / SQL · Owner: unassigned.**
Source: SEL-0037 reconciliation — 2026-09-15 review, low-severity report, reproduced 2026-09-22.

**Next action:** With relation fields that carry no `table`, JS, PHP, Python and C++ qualify every column by the join source's alias once a join is present (Python: `_column_ref({**field, 'table': source['table']})`, `translator.py` ~430), and Lisp renders the field's own spec (`column-ref tr (cdr cell)` / `(first matches)` in `index-binder`, `translator.lisp` ~482–495, and the ON-clause path): `SELECT name AS name, amount AS amount FROM orders o INNER JOIN customers c ON (customer_id = id)` against the other four's `c.name`, `o.amount`, `o.customer_id = c.id`. Different bytes, and ambiguous-column errors on a real engine when both tables share a name. The fuzz corpus binds every field to a table, which is why the host-versus-host lane never saw it. Add the case first (`sql/cases/26-links.sqlt`, both with aliases and without — the other four spell the unaliased right side `_2`), then make Lisp substitute the source alias as Python does.

**Close when:** The new cases pass byte-identical on all five; the SQL fuzz corpus gains a table-less field so the lane keeps watching.

**Resolution:** Resolved 2026-09-22. Cases first: three `link.qualify.*`
cases in `sql/cases/26-links.sqlt` (aliases on both sides; no aliases, where
the left renders under its table name and the joined side under `_2`; a
named binder) passed on JS, PHP, Python and C++ as written and failed on
Lisp. Probing first showed the divergence is join-only: single-relation
statements leave a table-less column bare in every host. The Lisp fix is one
helper, `qualified-by`, applied at the three places a joined statement
renders a row's field: the qualified index (`_1["X"]`, `translate-index`),
the other side's field found through the joined row, and the row's own
field (`index-binder`), the last using `joined-relation-alias` so the `_1` /
`_2` rows of a join predicate and the joined row after it resolve alike; a
RAW binding stays verbatim. All 860 SQL cases pass on all five. The fuzz
runners now bind AMOUNT without a table in every host (`tools/README.md`
says why), but the lane could not have watched this either way: it had
never passed the generator's `--sql` flag, so every pipeline read in-memory
lists and was refused before the translator (SEL-0048). Benchmark: the Lisp
SQL case suite 1,361 / 1,369 / 1,370 ms on the previous commit against
1,371 / 1,373 / 1,379 ms here (three more cases), and the five-host
benchmark against `6568201` within spread (table in the changelog).

<a id="sel-0043"></a>
### SEL-0043 — Positional index on a relation row: one refusal code and position

**Resolved 2026-09-22 · P2 · All five / SQL · Owner: unassigned.**
Source: SEL-0037 reconciliation — 2026-09-15 review, low-severity report, reproduced 2026-09-22.

**Next action:** `ORDERS .> MAP(RECORD("a", _[1]["AMOUNT"]))` and `ORDERS .> MAP(_["AMOUNT"]["Q"])` are refused as `E_SQL_BINDING` ("unknown joined relation '1'", the numeric index falling into the join-alias lookup) by JS, PHP and Python and as `E_SQL_SHAPE` by C++ and Lisp; inside a bucket (`… .> BUCKET(_["CUSTOMER_ID"]) .> MAP(RECORD("a", _[1]["AMOUNT"]))`) all five say `E_SQL_SHAPE`, but C++ reports 1:59 where the others report 1:56. Decide the code (E_SQL_SHAPE: a row of a relation is a map of named fields, and a position is a shape the row does not have — the accident is the alias lookup), pin the four shapes with positions in `sql/cases/09-refusals.sqlt`, then align JS, PHP, Python (code) and C++ (position). `tools/fuzz-sql.sh` compares codes and positions, so this is a red result waiting for the generator to emit a positional index on a binder.

**Close when:** The cases pass on all five; `tools/gen-programs.mjs` emits the shape.

**Resolution:** Resolved 2026-09-22. Analysed first: a 26-shape probe of
the nested index `_[k][field]` through every host's statement and plan
lanes. The rule that fell out: `k` is a qualifier when it names a relation
of the statement — its binding name, its table, its alias, or a binder the
join predicate declared — and every other `k` (a position, a text "1", a
stray name, a field) is the shape the row does not have: `E_SQL_SHAPE` at
the outer index. Four divergences, not one: (1) JS, PHP and Python answered
`E_SQL_BINDING` "unknown joined relation" for the non-qualifier case, the
alias lookup's accident; (2) C++ reported a bucket's projected row at the
outer bracket where the other four refuse at the inner one, because its
pre-walk of the inner index accepted only a text key; (3) C++ accepted the
positional `_`, `_1`, `_N` and Lisp `_1` as qualifiers, so `_["_2"]["NAME"]`
after a join was pure SQL there and pure memory elsewhere; (4) C++ rendered
the qualified column bare in a single-relation statement where the other
three that reach it qualify it by the relation's alias. All four fixed to
the rule. Coverage: thirteen `refuse.index.*` statement cases pin every
refusing variant with its position (positions are the index's own bracket),
six `plan.index.*` planner cases pin the qualified reads that succeed and
the three positional names that do not, and `tools/gen-programs.mjs` now
emits a nested index on a row binder (a position, a text position, a stray
name, an alias) so both fuzz lanes compare the codes and positions from
here on. Validation: 879 SQL cases on all five, language fuzz 4,000
programs 0 disagreements, SQL fuzz 0 disagreements on every dialect,
hybrid checks 135 (JS, PHP), pytest 630, Lisp unit 532, C++ unit 171 and
SQL unit 85, C++ conformance 935. Left as found, and noted for SEL-0047:
`translate_statement` refuses the successful qualified read at 0:0 in every
host while `plan_hybrid` renders it, so the positive forms are pinned as
planner cases.

<a id="sel-0044"></a>
### SEL-0044 — Probe physical_ast and plan_hybrid in the API-parity lane

**Resolved 2026-09-22 · P3 · All five / validation · Owner: unassigned.**
Source: SEL-0037 reconciliation — 2026-09-15 review, critic gap.

**Next action:** `tools/api.*` (the 54-probe `tools/check-api.sh` lane) mentions neither `physicalAst()`/`physical_ast()`/`program-physical-ast` nor any of `plan_hybrid`'s fields. Add probes for the physical tree's identity rule (built once, dropped when the AST is reassigned) and for each planner field on one pure-SQL, one hybrid and one pure-memory program.

**Close when:** The lane's probe count rises accordingly and every host answers the same lines.

**Resolution:** Resolved 2026-09-22, after SEL-0049 fixed the rule the
identity probe asserts. Four `program.physical.*` probes in the five `api`
runners (built once; the AST's dependencies unchanged by the build; run
agrees on a join over real rows after an explicit build; the same tree after
runs over two different contexts) — 64 probes, all seven roster entries
agree. Five new `sqlapi` runners (`tools/sqlapi.mjs`, `tools/sqlapi.php`,
`python/bin/sqlapi`, `cpp/bin/sqlapi.cpp`, `lisp/bin/sqlapi`), an
`impl_sqlapi` entry, `tools/check-sqlapi.sh` and a "host SQL API parity" gate
step: 27 probes over one pure-SQL, one hybrid and one pure-memory program —
kind, dialect, statement, prefix and continuation presence, the
continuation's dependencies, source variable, tables, selected member — the
five hosts with a SQL layer agree on every one; the JS bundles print nothing
and are left out, as the case runner leaves them out. Values are compared,
not spellings. The analysis below stands as written.
Analysed 2026-09-22 first, no change then; the lane had
60 probes, not 54. Two constraints shape the work. (1) The C++ probe
binary is in the Makefile's LINKED list — "sel.hpp alone is enough" is a
checked claim — and the JS runner serves js-bundle and js-bundle-min through
`SEL_JS_ENTRY`, whose bundle carries no SQL layer; so the planner probes
cannot go into `tools/api.*` and need a second runner set (`sqlapi`), run for
the SQL-capable hosts only, exactly as `impl_sql` skips the bundles. The
physical-tree probes are core-only and belong in the existing runners.
(2) A finding the probes would surface at once: Python's `physical_ast`
takes a context and rebuilds whenever the context object changes, because its
join-filter pushdown reads the rows' keys to decide which side of a LINK a
field belongs to (`optimizer.py` `pushdown_join_filters` / `get_table_columns`);
the other four build once per AST and decide from the tree alone. Verified:
`ORDERS .> LINK(CUSTOMERS, …) .> FILTER(_["amount"] > 1 AND _["name"] $== "x")`
yields one tree in JS and in Python without a context, and a different one in
Python with rows (the amount filter pushed under the LINK). Data-dependent
physical trees are unobservable in results when correct, but they break the
"built once" identity rule the item wants to pin and are a Python-only
behaviour nobody else has — opened as SEL-0049 to decide before the probe is
written (align Python to a tree-only rule, measuring the scale scenarios, or
document the exception and probe identity without a context). Suggested
probes, files and shape are in the 2026-09-22 session report and summarised
here: `program.physical.*` (built once; reassigning `ast` drops it; the AST is
unchanged by the build; run agrees with and without an explicit build) in the
five `api` runners; `plan.*` values (classification, dialect, statement text,
prefix and continuation presence, continuation dependencies, source var,
source tables, selected member) for one pure-SQL, one hybrid and one
pure-memory program over fixed ORDERS/CUSTOMERS bindings in five new
`sqlapi` runners, an `impl_sqlapi` entry, a `check-sqlapi.sh` driver (or the
existing driver parameterised by runner) and a gate step; values, not
spellings, since the snake/camel aliases are host convention.

<a id="sel-0045"></a>
### SEL-0045 — Sanitize the C++ SQL layer and planner

**Resolved 2026-09-22 · P3 · C++ / validation · Owner: unassigned.**
Source: SEL-0037 reconciliation — 2026-09-15 review, critic gap.

**Next action:** `make asan` compiles `tests/unit.cpp` and `bin/conformance.cpp` alone; `sel_sql*.cpp`, `sel_sql_hybrid.cpp` and `bin/sqlt.cpp` have no sanitizer target, so a leak or an out-of-bounds in the translator or planner is caught by nothing in the tree. Add `sqlt-asan` (and `sqlunit-asan`) to the `asan` target, built from the SQL objects with the same flags.

**Close when:** `make asan` runs the SQL case suite and the SQL unit binary under the sanitizers, clean.

**Resolution:** Resolved 2026-09-22. `make asan` now builds and runs four
binaries: `unit-asan` and `conformance-asan` as before, plus `sqlunit-asan`
(executed plans, the shared physical tree) and `sqlt-asan` (stage 1, the
translator and the planner over all 879 committed cases). The sanitized
objects of the core and the nine SQL sources are compiled once into
`build/asan/` and shared by three of the binaries, and the target is
incremental (a second `make asan` compiles nothing). First run: unit
171/171, conformance 935, SQL unit 85/85, SQL cases 879, no sanitizer report.
Confirmed load-bearing by a probe: a deliberate heap overflow in
`Translator::translate` of a scratch worktree made `sqlt-asan` abort with
`heap-buffer-overflow` at the injected line. The optimised build is untouched
(the normal targets and flags are the same): C++ Mandelbrot 148.6 / 147.9 →
146.3 / 148.8 ms against `6568201`, conformance 126 → 129 ms with ten more
cases, the SQL case suite 192 / 180 / 183 → 185 / 185 / 189 ms against the
previous commit, startup ~2.2 ms per process. `tools/check.sh` still leaves
`make asan` out deliberately, as the README says: it takes minutes.

<a id="sel-0046"></a>
### SEL-0046 — Compare refused-plan error positions in every runner

**Resolved 2026-09-22 · P3 · All five / validation · Owner: unassigned.**
Source: SEL-0037 reconciliation — 2026-09-15 review, low-severity report.

**Next action:** For a `--- plan refused` case every runner parses `CODE line:col` and compares only the code (Python `python/bin/sqlt` ~172; Lisp `(declare (ignore line col))`), and `tools/gen-sql-cases.mjs` accepts a position it will never assert. Either compare the position when one is written, in all five runners, or have the generator refuse a position on a refused plan.

**Close when:** A refused-plan case with a wrong position fails on every host, or cannot be written.

**Analysis (2026-09-22):** Confirmed on all five runners with probe cases (added, run, and
removed again; the tree is as it was): a `--- plan refused` case whose
`--- error` says `E_SQL_DIALECT 9:9` or `E_SQL_BINDING 9:9` PASSES on js,
php, python, cpp and lisp — every runner parses the position and compares
the code alone, where its translate path compares both. With the five
refused branches patched to compare a written position (the translate
path's check, copied), the same probes fail identically on every host: `got
it at 0:0`. That is the finding under the finding: **a plan refusal carries no
program position on any host** — the two refusals planning can raise, a base
dialect and an alias collision, blame the bindings or the dialect, not the
rule, and every host's `SqlError` reports `0:0` when no node is blamed. The
only refused-plan case in the suite (`plan.refuse.base-dialect`) writes no
position, correctly. So the option "compare the position when one is
written" would pin only `0:0`, a position that is not one. A third probe
showed the boundary: a program the translator refuses with `E_SQL_DEPTH`
(`A = 1;` before 200 terms, or a 200-term predicate over a bound relation) is
not refused by `plan_hybrid` on any host — it is classified `pure_memory`,
on all five alike, so the planner never refuses for a reason inside the
program, and running such a plan raises `E_DEPTH` in the evaluator.
Recommended: (1) have `tools/gen-sql-cases.mjs` refuse a position on a
refused plan (it already refuses `--- expect` and `--- tables` there) and say
in sql/cases/README.md's `plan` row that a refusal blames the bindings or the
dialect and carries no position; leave the five runners comparing the code
and replace Lisp's `(declare (ignore line col))` and its four twins with a
comment saying why (the position is never written); (2) pin the depth
boundary as a decision with two planner cases — the 200-term chain behind
`A = 1;` and the 200-term predicate over a relation are `pure_memory` — or
decide instead that the planner should refuse what stage 1 refuses, in which
case `E_SQL_DEPTH 1:8` becomes the one refusal with a program position and
the runners must compare it (the patch is the translate path's five-line
check, copied into each refused branch; it was applied and reverted here).

**Resolution:** Resolved 2026-09-22, working tree on `b274aa2` (commit
pending), by the second closing condition — the case cannot be written.
`tools/gen-sql-cases.mjs` now refuses a `--- plan refused` case whose
`--- error` carries a position (a probe with `E_SQL_DIALECT 1:1` stops the
generator with "a refused plan blames the bindings or the dialect, not a
position"), and sql/cases/README.md says why beside the `plan` rule: planning
refuses only on the bindings or the dialect, which blame no node of the rule,
so the error carries no position, and a refusal that blames a place in the
program is a translate case. The five runners keep comparing the code, and
each refused branch now says so in a comment where Lisp's
`(declare (ignore line col))` stood unexplained. The depth boundary is pinned
as a decision by two planner cases, `plan.pure-memory.past-the-depth-limit-
falls-back` (200 terms behind `A = 1;`, no source table) and
`plan.pure-memory.past-the-depth-limit-over-a-relation` (the 200-term
predicate over a bound relation, `orders` still named): both `pure_memory` on
all five, beside `plan.pure-memory.non-normalisable-falls-back`, whose note
already states the rule that a program stage 1 refuses is a plan the database
does none of. Full SQL check: 881 cases per host (from 879); generated
artifacts, dialect map and case data current; SQL API 27 probes and the map
replay agreeing on five hosts; SQL docs 27 quoted examples right; with the
gate's Docker servers, mutations 197 caught, 0 survived, 0 skipped; oracle 0
differing on every dialect; SQL fuzz 2,000 programs 0 disagreements host
versus host and 0 differing against every server.

<a id="sel-0047"></a>
### SEL-0047 — Cover binding kinds and multi-line programs under plan_hybrid

**Resolved 2026-09-22 · P3 · All five / SQL · Owner: unassigned.**
Source: SEL-0037 reconciliation — 2026-09-15 review, critic gaps.

**Next action:** `Binding.raw`, relation `scalar` / `correlate` / `prefilter` bindings and `Binding.columns()` appear in a handful of `.sqlt` cases and none under `plan_hybrid` / `execute_hybrid`; multi-line programs (positions with line > 1) through the hybrid continuation were probed only by hand. Add planner cases for each binding kind and one multi-line program whose continuation error is reported at its line.

**Close when:** The cases pass on all five.

**Analysis (2026-09-22, before the change):** *The gap is real, and it is coverage, not behaviour.* Of 168 planner cases,
the bindings under `--- plan` use plain `column` and `relation` bindings, one
relation query (`plan.tables.relation-query-is-reported-verbatim`) and four
raw fields (`plan.group-identity.raw-derived-prefix.*`); none uses a
`correlate`, `scalar` or `prefilter` relation, a `columns` binding or a
top-level `raw` binding, and no planner case has a source of more than one
line. The five per-host continuation tables (12 identical rows each:
`tools/check-js-optimizer.mjs`, `tools/check-php-optimizer.php`,
`python/tests/test_unit.py`, `cpp/tests/sql_unit.cpp`, `lisp/tests/unit.lisp`)
are single-line too. Probed with temporary cases and scripts, all reverted:
every host agrees today — a correlated relation with a `scalar` under
`ANY` in a FILTER over ORDERS is `pure_sql` with tables `orders, order_items`;
a `prefilter: separate` relation renders the two-`EXISTS` form; a `columns`
binding and a top-level `raw` binding in a body are `pure_sql` over `orders`;
a three-line source plans as a `hybrid` whose prefix is `ORDER BY`; a
correlated relation read only in the continuation reports `orders` alone
(tables are read off the prefix tree in every host) and `orders, order_items`
when the prefix reads it; and a helper on line 1 read on line 3 reports
`E_NOT_NUM@3:6` from both `run()` and the executed plan on JS, PHP, Python,
Lisp and C++. So the cases will pin agreement, not find a divergence.
*Plan.* (1) `sql/cases/25-hybrid-plans.sqlt`, a `plan.bindings.*` block:
`correlated-relation-with-a-scalar` (pure_sql, `orders`/`order_items`),
`correlated-relation-in-the-prefix-of-a-hybrid` (hybrid, both tables),
`correlated-relation-only-in-the-continuation` (hybrid, `orders` — the
decision that tables are what the *database* reads; say so in a note and in
§12.1's `source_tables` row), `prefilter-separate-relation` (pure_sql, the
two-`EXISTS` statement), `columns-in-a-body` and `raw-in-a-body` (pure_sql,
`orders`); and a `plan.multi-line.*` pair: the three-line hybrid and
`A += 1;` on its own line before the pipeline (pure_memory). Nothing is
written per host: `node tools/gen-sql-cases.mjs` renders each into the five
runners' case data — for Lisp, a `:bindings (lambda () (list (cons "ITEMS"
(binding-relation "order_items" "oi" (list (cons "QTY" (binding-column "qty"
"oi" :num))) "QTY" "`oi`.`order_id` = `o`.`id`"))))` thunk and a `:source`
string with embedded newlines, exactly as `warrant.numeric.the-guard-reaches-
into-a-relation-body` and `mode.params.join-separator-is-bound-per-gap` are
rendered today; JS gets the same as `Binding.relation("order_items", "oi",
{...}, "QTY", "…")`, and PHP, Python and C++ their constructors. (2) The
continuation error at its line cannot be a `.sqlt` case — a planner case
executes nothing, and a refused plan carries no position (SEL-0046) — so one
row goes into each of the five tables: `"X = ORDERS .> TAKE(2);⏎X .>
MAP(COUNT(X) + _[\"id\"]⏎   + \"x\")"`, `hybrid`, `E_NOT_NUM@3:6`. In
Lisp the table is a quoted literal list, so the row is a string literal with
real newlines inside it (not `format` and `~%`), beside the `E_NOT_NUM@1:54`
row at `lisp/tests/unit.lisp` ~1226; JS, PHP, Python and C++ write `\n` in
their string literals beside the same row. *Risks.* The expectations are
copied from today's output, so they pin behaviour rather than a written
decision — the one decision worth writing first is `source_tables` for a
relation only the continuation reads (`orders` alone; the continuation reads
`ITEMS` from the caller's context at run time, which is not a physical
source). The generator trims a section, so a multi-line source must start
flush left or its line-1 columns shift; lines 2+ keep their indentation. The
`prefilter` expectation is long and MariaDB-specific, and the runner mirrors
MariaDB cases onto MySQL automatically. Test-only change: no runtime code,
no generated map, no gate-time cost worth measuring. The larger option — a
`--- run` section so the twelve per-host rows become shared `.sqlt` data —
would be a format change touching all five runners and the generator; not
proposed here.

**Resolution:** Resolved 2026-09-22, working tree on `0f9031d` (commit
pending), as planned. Eight planner cases in `sql/cases/25-hybrid-plans.sqlt`:
`plan.bindings.correlated-relation-with-a-scalar` (pure_sql; `orders`,
`order_items`), `…-in-the-prefix-of-a-hybrid` (hybrid; both tables),
`…-only-in-the-continuation` (hybrid; `orders` alone — the decision, now
written in docs/SQL-TRANSLATION.md §12.1's `source_tables` promise: a hybrid
plan reports what its prefix reads, and a relation the continuation reads
comes from the caller's context at run time), `prefilter-separate-relation`
(pure_sql, the two-`EXISTS` statement; `orders`, `cms_fields`),
`columns-in-a-body` and `raw-in-a-body` (pure_sql; `orders`),
`plan.multi-line.hybrid-splits-across-lines` (a three-line source, `ORDER BY`
prefix) and `plan.multi-line.statement-on-its-own-line-is-pure-memory`. The
generator rendered them into the five runners' case data; no host code. One
row in each of the five continuation tables — the three-line helper program,
`hybrid`, `E_NOT_NUM@3:6` — in `tools/check-js-optimizer.mjs`,
`tools/check-php-optimizer.php` (a double-quoted string, since PHP's single
quotes keep `\n` as two characters), `python/tests/test_unit.py`,
`cpp/tests/sql_unit.cpp` and `lisp/tests/unit.lisp` (real newlines inside the
literal of the quoted list). Results: 889 SQL cases per host (from 881); JS
optimizer 142 checks, PHP 139, pytest 635, C++ SQL unit 85, Lisp 553; SQL
docs 27 and language docs 150 examples right; `tools/check.sh` ALL GREEN.

<a id="sel-0048"></a>
### SEL-0048 — SELECT_COLS after a sort wrapped the plan in four hosts; the SQL fuzz lane never ran the generator's SQL mode

**Resolved 2026-09-22 · P2 · All five / SQL · Owner: unassigned.**
Source: found while closing SEL-0042.

**Next action:** Done. (a) `tools/fuzz-sql.sh` generated its corpus without
`--sql`, so every pipeline read the prelude's in-memory lists and was refused
as unbound before reaching the translator or the planner: 66 join programs,
0 translated. The lane now passes `--sql`, as `tools/README.md` and the
generator's own comment always said it should. (b) Its first run in that mode
found one disagreement: `CUSTOMERS .> SORT_BY(r, r["id"], "DESC") .>
SELECT_COLS("name")` planned as `SELECT _sub1.name FROM (SELECT c.* FROM
customers c ORDER BY c.id DESC) _sub1` on JS, PHP, Python and C++ and as
`SELECT c.name FROM customers c ORDER BY c.id DESC` on Lisp. Lisp was right:
the MAP arm's own comment records that a derived table is where MariaDB
drops an ORDER BY with no LIMIT beside it, and the four hosts' SELECT_COLS
arm was testing "anything above the rows" instead of the MAP rule. The four
now use the MAP predicate; `plan.select-cols.after-a-sort-keeps-one-statement`
pins the flat statement.

**Close when:** The case passes on all five; the SQL fuzz lane reports 0 disagreements in SQL mode.

**Resolution:** Resolved 2026-09-22: 860 SQL cases on all five, the SQL fuzz
lane 0 disagreements on every dialect in SQL mode, hybrid checks 135 (JS,
PHP), pytest 630, Lisp unit 532, C++ SQL unit 85. Still thin: only 5 of the
corpus's 66 join programs receive a plan, so the join seam SEL-0042 fixed
is pinned by its cases rather than by the fuzzer; widening the generator's
translatable join shapes is a fair follow-up under SEL-0047.

<a id="sel-0049"></a>
### SEL-0049 — Python's physical tree depends on the context's data; the other four depend on the AST alone

**Resolved 2026-09-22 · P2 · Python · Owner: unassigned.**
Source: SEL-0044 analysis, 2026-09-22.

**Next action:** `Program.physical_ast(context)` in `python/sel/__init__.py` is keyed by the AST *and* the context object, and `optimizer.pushdown_join_filters` reads the context's rows (`get_table_columns`) to decide which side of a LINK an unqualified field belongs to; JS, PHP, C++ and Lisp take no context and decide from the tree. Consequences: a fresh context per run rebuilds the optimised tree (SEL-0030 saw this cost as optimiser garbage), the tree a program runs can differ by data, and the "built once per AST" identity rule of docs/SQL-TRANSLATION.md §12.1 is false for Python. Decide: either make Python's pushdown tree-only like the other four (measure S1–S6 on `tools/scale-test` before and after, since the pushdown exists for those joins), or keep it and document it as a Python-only physical optimisation with its own identity rule. Then SEL-0044's identity probes can be written to the decided rule.

**Close when:** The rule is written in §12.1 and pinned by a Python unit test; the scale scenarios measure no worse than the recorded figures; SEL-0044's probes pass on all five.

**Resolution:** Resolved 2026-09-22, the first way: Python's physical tree
is a function of the AST alone. `physical_ast()` takes no context and is
keyed by the AST like the other four; `optimize_ast` and the join-filter
pushdown take no context or schema; an unqualified field of the joined row
names no side (JS's rule: "ambiguous"), only a qualified `_["O"]["f"]` does.
§12.1 says so; `test_physical_tree_is_a_function_of_the_ast_alone` pins it.
The data-driven pushdown was also unsound — it read the FIRST row's keys to
decide a side, and rows may differ — so its speed was borrowed. Its sound
form now lives in the evaluator: a FILTER whose source is a LINK hands the
join its leading field conjuncts; the join pre-applies them to left rows
where the joined row's field is provably the left row's (the field is a key
of no right row, over every row), keeps a row on any error so the full
predicate raises where it would have, numbers the kept rows as the
unfiltered join would (FILTER keeps its input's keys), travels down a chain
of joins when both sources are pure, and does nothing on the nested-loop
join, whose numbering would need the scan it avoids. Two tests pin results,
keys and errors against the same program evaluated through a helper
variable. Scale scenarios, before → final: S1 1,997 → 1,983 ms, S2 37.5 →
37.5, S3 925 → 917, S4 62.0 → 62.4, S6 793 → 781, and **S5 630 → 1,382 ms**
(1,175–1,224 isolated): the pre-filter reaches only the outermost of S5's
three joins, because the logical optimiser (every host) has pushed the
qualified `_["orders"]["order_year"]` conjunct below it as a FILTER, and
carrying conjuncts through that FILTER would change which error surfaces on
a dropped row. The JS host runs S5 in 529 ms; Python's 1.2–1.4 s is the
proportion its other scenarios show, and the 630 ms was the unsound rewrite's.
Recovering it soundly is SEL-0050.

<a id="sel-0050"></a>
### SEL-0050 — Carry Python's join pre-filter through a pushed FILTER to recover scenario 5

**Resolved 2026-09-22 · P3 · Python · Owner: unassigned.**
Source: SEL-0049 closure, 2026-09-22.

**Next action:** Scenario 5's leading conjuncts (`status`, `tier`) cannot travel below the FILTER the logical optimiser pushed between its joins: a row dropped before that FILTER would skip the error its predicate might raise, and dropping rows inside it would renumber the join above. One sound design: hand the conjuncts through the FILTER as *marks* rather than drops — a marked row still passes the intermediate predicate (so its error surfaces in order), the join above computes its key and match count for a marked row and advances its numbering without emitting — so the base joins shrink as the old rewrite made them. Measure S5 (`tools/scale-test/sel_benchmarks.py --only scenario5`) and the whole set; pin results, keys and errors as SEL-0049's tests do.

**Close when:** S5 measures near its former 630 ms with results, keys and error order unchanged; the physical tree stays a function of the AST.

**Resolution:** Resolved 2026-09-22, by a different route than the marks
sketched above. The conjuncts travel as STAGES, one per FILTER in the order
the FILTERs run: a FILTER between two joins (the one the logical optimiser
pushed) takes the stages handed down by the join above, puts its own whole
predicate first — only when every conjunct of it is pre-evaluable, so a row
dropped below could not have raised in it — and hands them to its own join;
each join applies the stages in order with the keep-on-error rule and cuts the
list at the first conjunct that reads a field its right rows have. Deep drops
(below the join directly under the owning FILTER) change that FILTER's keys,
so the physical optimiser stamps each FILTER body with whether a following
step renumbers without reading `_K` (`keys_unobserved`, the same notion the
logical rewrites use; a new field on this host's node) and deep drops happen
only then; otherwise the join under the FILTER keeps the numbering as before.
Three costs were then taken out of the sound path: a join reports upward how
many conjuncts every row it emitted has passed and whether any row was kept
on an error, so the join above re-applies only the rest (or everything, after
an error); the right side's keys are gathered once per record shape, and once
per identity scan when a dense list shares one shape; and a row that carries a
literal join key (`_1["customer_id"]`) is rejected before its key is computed,
since the read could only have raised for a row without the field. Scale
scenarios, before → now: S1 1,997 → 2,022 ms, S2 37.5 → 37.7, S3 925 → 931,
S4 62.0 → 61.6, S6 793 → 776, **S5 630 → 698 ms** (from 1,382 at SEL-0049's
close). The residual 11% is the price of soundness: the old rewrite also
skipped the join key of every row it dropped, and never checked the right
rows' keys. A third parity test pins results, keys and errors on the deep
path against the same physical tree with the pre-filter switched off — the
helper-variable oracle does not serve there, which is SEL-0051.

<a id="sel-0051"></a>
### SEL-0051 — The logical FILTER pushdown evaluates a pushed conjunct on rows an earlier conjunct would have short-circuited

**Resolved 2026-09-22 · P3 · All five · Owner: unassigned.**
Source: SEL-0050's tests, 2026-09-22.

**Next action:** `ORDERS .> LINK(CUSTOMERS, …) .> LINK(ITEMS, …) .> FILTER(_["status"] $== "A" AND _["orders"]["amount"] > 2 AND _["sku"] $== "s1")` raises `E_NOT_NUM` in every host when an order with status B carries a text amount, because the qualified conjunct is pushed below the upper join and evaluated on rows the source program's `AND` would have skipped; through a helper variable (`J = …; J .> FILTER(…)`) the same program answers rows. The five hosts agree, so parity holds, but docs/SQL-TRANSLATION.md §12.1's "a rewrite keeps the program's value, or it does not fire" is not true of errors here. Decide: document the rule as "a pushed conjunct may be evaluated where its short-circuit would not have reached" (and say so beside the rewrite in every host), or restrict the pushdown to conjuncts that precede no unpushed one. Measure S5 on every host before choosing the second.

**Close when:** The rule is written in §12.1 and a `.selt` case pins the chosen behaviour on all five.

**Analysis (2026-09-22, before the change):** Proven on all five hosts with one batch corpus (js, php, cpp,
lisp, python answer identically): as written, `ORDERS .> LINK(CUSTOMERS,
_1["customer_id"] == _2["id"]) .> FILTER(_["status"] $== "A" AND
_["orders"]["amount"] > 2)` over an order with status B and amount "x" is
`E_NOT_NUM` at 1:295; `J = ORDERS .> LINK(…); J .> FILTER(…)` returns the
one row; with the conjuncts swapped both forms raise (the control); the same
predicate over a plain list returns the row. It is the PHYSICAL join
predicate pushdown, not the logical tier: the logical tree is unchanged and
the physical tree is `MAP{FILTER{LINK{FILTER{ORDERS} pred=(_['amount'] > 2)}}
pred=(_['status'] $== 'A')}` — each conjunct is classified by the side it
reads and moved on its own, whatever its place in the AND chain
(`pushdownJoinFilters` in every host), so a conjunct the source program's
short-circuit would never have reached on that row is evaluated there. The
planner and SQL translation are not involved. Nothing pins it: the three
`rel.link.then-filter.*` cases pin name resolution after a LINK (review W2),
not evaluation order. What the pushdown is worth: JS scenario 5 is 508 ms
with it and 706 ms running the logical tree alone. Options: (A) document —
spec §7 and docs/SQL-TRANSLATION.md §12.1 say a pushed conjunct may be
evaluated on rows an earlier conjunct would have rejected, and a `.selt`
case pins the raise; (B) push only conjuncts that precede no unpushed one —
exact source semantics, costs the four fast hosts ~40% on S5 and Python
more; (C) push tentatively — the pushed FILTER keeps a row its predicate
raises on and the conjunct stays in the top predicate, so errors surface in
source order and the row-shrinking is kept (the rule Python's run-time
pre-filter already lives by); a flag on the pushed node in every host, one
line in each evaluator's FILTER, and the top predicate re-evaluates a passed
conjunct on surviving rows — recommended. Session report 2026-09-22 carries
the code sketches.

**Resolution:** Resolved 2026-09-22, working tree on `b274aa2` (commit
pending), by option C made exact. Spec §7.4 now says a `FILTER` after a
`LINK` is evaluated as written, and draws the consequence that a conjunct is
tested early only when every conjunct before it is tested there too. Nine
`rel.link*.then-filter.*` cases pin it on five hosts: the pushed conjunct
keeps the source order (left, right, `LINK_LEFT`), raises where the source
does, two raising conjuncts report the first, a conjunct after a raising one
is not tested early, a right conjunct after a left one is not tested early,
and the conjunct after the tested run still filters, and raises in its place.
Option C as sketched had a hole that writing those cases found: a pushed
conjunct that is FALSE on a row drops it, and a conjunct EARLIER in the `AND`
that reads the joined row — `_["customers"]["credit"] > _["orders"]["id"]` —
would have raised on that row's join; the tentative keep covers only the
pushed conjunct's own raise. Proven on JS and Python before the fix: the
program as written `E_NOT_NUM`, the tree an empty list. So the rewrite in
every host now moves only the LEADING run of conjuncts that name one side (a
left run, or a right run under an inner join), and moves it tentatively: a
raise in the pushed body keeps the row and bumps a counter on the context;
the `FILTER` above keeps its whole predicate, marked `pushed_down`, with the
conjuncts that did not move as `remaining` (`TRUE` when all did), and
evaluates only `remaining` when the counter did not move while its source ran
— every row it sees then passed the pushed run, deterministically, since that
run read only its own side's row — or the whole predicate, in source order,
when a row was kept on an error; a `TRUE` remainder returns the join's list
as it is. FILTER fusion merges bodies of equal tentativeness only and carries
the marks and the remainder. The marks are `tentative`/`pushedDown`/
`remaining` on JS and PHP nodes, dataclass fields on Python's, `Node` members
in C++ (`sel_ast.hpp`), slots in Lisp (`copy-node-shallow` copies them).
Shape checks updated: `tools/check-js-optimizer.mjs` (139),
`tools/check-php-optimizer.php` (136), `python/tests/test_unit.py` (634),
`cpp/tests/unit.cpp` (173), `lisp/tests/unit.lisp` (550). Scale scenarios,
HEAD `b274aa2` against this tree, interleaved, seven runs after two warm-ups:
S1–S4 and S6 within run-to-run spread on every host (S1: C++ 561.9 → 550.7
ms, JS 616.0 → 601.0, PHP 3,468.6 → 3,484.2, Python 2,003.9 → 2,028.1, Lisp
469.0 → 462.0; S3: 176.0 → 174.2, 231.4 → 224.3, 1,103.9 → 1,110.6, 916.5 →
928.8, 225.0 → 235.0). **Scenario 5 is slower on the four hosts whose speed
there came from the unsound rewrite: C++ 523.7 → 634.5 ms (+21%), JS 493.7 →
695.7 (+41%), PHP 2,207.8 → 3,132.7 (+42%), Lisp 396.0 → 527.0 (+33%)** — its
predicate opens with two unqualified conjuncts (`_["status"]`, `_["tier"]`),
which name no side (SEL-0049), so the qualified `_["orders"]["order_year"]`
after them no longer moves under the joins; nothing else in the scale set has
a conjunct after an unpushed one. Python, whose run-time pre-filter proves
side ownership from the data, is 1,361.9 → 902.3 ms against HEAD (−34%), but
was 698 with the uncommitted SEL-0050 tree that still had the unsound push
(that staging cuts its list at `tier`, a customers field, and leaned on the
pushed FILTER to have thinned ORDERS below it). Recovering S5 exactly is
SEL-0052. Validation: 944 conformance cases on seven roster entries;
differential fuzz 4,000 programs and SQL fuzz 2,000 programs with 0
disagreements; API 64, SQL API 27 and the end-to-end scenarios agreeing;
`tools/check.sh` ALL GREEN (49 layers) with the gate's own Docker servers,
197 mutations caught; no compiler warning.

<a id="sel-0052"></a>
### SEL-0052 — Recover scenario 5 exactly: a run-time join pre-filter that proves side ownership and totality, in every host

**Resolved 2026-09-22 · P3 · All five · Owner: unassigned.**
Source: SEL-0051 closure, 2026-09-22.

**Next action:** The physical pushdown can move only a leading run of one-sided conjuncts (SEL-0051), and a predicate that opens with unqualified reads — scenario 5's `_["status"] $== "COMPLETED" AND _["tier"] $== "PLATINUM" AND _["orders"]["order_year"] == 2026` — moves nothing, which costs JS, PHP, C++ and Lisp 21–42% there against the unsound rewrite. What the tree cannot know, the join can: Python's `_link` already pre-applies a FILTER's leading conjuncts to left rows where the read field is a key of no right row (SEL-0049/0050), and reached 698 ms while the unsound push still thinned ORDERS below it, 902 without. Two steps. (1) Extend Python's staging so a conjunct may be applied below an earlier one that is *total* over the joined rows — its field owned by one side and present, with the operator's type, in every row of that side (`$==` on `tier` needs a text in every customer), a per-relation scan cached beside the key sets — so `order_year` reaches ORDERS while `tier` waits for the customer join; the report upward becomes a set of passed conjuncts rather than a count. (2) Port the pre-filter — stages, reports, key gathering, the literal-key fast path and the totality proof — to the other four hosts, whose `LINK` builtins take the same shape. Measure S5 per host with the scale harness against `b274aa2`; the rest of the set must stay flat.

**Close when:** S5 is within run-to-run spread of the `b274aa2` figure on every host (C++ 524 ms, JS 494, PHP 2,208, Lisp 396; Python 698, the SEL-0050 figure) with the SEL-0051 cases green; the physical tree stays a function of the AST.

**Resolution:** Resolved 2026-09-22, working tree on `0f9031d` (commit
pending). *Designs weighed.* (a) More reordering in the physical tree —
rejected: which side owns an unqualified field, and whether a conjunct can
raise, are facts about data, and the tree must stay a function of the AST
(SEL-0049). (b) SEL-0050's first sketch, drop-marks carried through every
join — rejected: every join would carry per-row mark state and still need the
same proof. (c) A run-time pre-filter in every host's `LINK`, extending
Python's SEL-0049/0050 one with a totality proof — chosen, prototyped in
Python (the lane that already had a pre-filter, and the only one whose
drops reached below a join), validated there, then ported. *The mechanism*
(docs/EXTENDING.md has the full rule): a FILTER over a LINK hands the join
all its conjuncts with the fields each reads and, for a comparison of
literals and bare fields, the (field, kind) requirements under which it
cannot raise; the join applies to its left rows those whose fields the
joined row takes from them, passes over a total one, stops at anything else,
keeps a row a conjunct raises on, and reports upward which conjuncts every
row passed. S5's `status` and `tier` are total (every order and every
customer carries them as text), so `order_year` reaches ORDERS below all
three joins. *What validating the prototype found.* A new differential
oracle (`tools/join-filter-oracle/`: join-then-filter programs over small
relations with missing, null, boolean and non-numeric fields, each as
written and with every join result bound to a helper variable, where no
pre-filter can apply) showed the first prototype and SEL-0050 itself losing
errors in three ways, each fixed in Python first: a join below the FILTER's
own join skipped the upper joins' key evaluation for dropped rows (an
`E_NO_KEY` in `L["A"]["cid"]` lost) — each handing join now sends its left
key down and the dropping join proves it present on every row; a joined row
promotes a left field only when the left input's FIRST row has it (§7.4) —
ownership now requires that, and drops below the FILTER's own join require
one key set per relation and no null or null-extended right rows, since a
drop changes which row comes first into the joins above; and the hand-down
read the optimiser's tentative-keep state before the join's right side had
run. Also: only the FILTER's own binder, exactly as named, is the element.
Python's oracle mismatches fell by 7–11 per 3,000-pair corpus (its
SEL-0050 losses); no host gained one (Lisp's five on mixed-shape data are on
rows its own join already builds wrongly, SEL-0053). Fifteen conformance
cases pin the boundaries (`passed-over.*`, `early.*`, `nested-read-*`).
*Scale*, `0f9031d` against this tree, interleaved, seven runs after two
warm-ups: S5 C++ 647 → 277 ms, JS 675 → 276, PHP 3,129 → 1,165, Lisp 559
→ 261, Python 897 → 786 — below the `b274aa2` figures on four hosts (524,
494, 2,208, 396) and 42% below Python's (1,362); Python's 698 of SEL-0050
was the unsound drops' figure, and the 13% between is the three checks.
S1–S4 and S6 within run-to-run spread on every host (a first-cut C++ S6 +6%
came from gathering facts behind an `IS_NULL` conjunct; the FILTER now hands
nothing when its first conjunct can neither be applied nor passed over).
*Validation:* 959 conformance cases on five hosts; pytest 635, JS optimizer
142, PHP optimizer 139 and runtime 35, C++ unit 173 and SQL unit 85, Lisp
553, SQL cases 889; `make asan` clean; the differential oracle 15,000 pairs
× five hosts; `tools/check.sh` ALL GREEN (49 layers, 590 s) with the gate's
Docker servers, 197 mutations caught; and on real servers — the 10x fixtures
loaded into Docker PostgreSQL 17 and MariaDB 11.8, the scale harness's
database lane executed S1–S5's planned SQL and matched the reference rows
exactly on both, the same reference every host's in-memory run (with the
pre-filter) matches (S6 could not run there, SEL-0055). *Amended by
SEL-0053 (2026-09-23):* joined rows are now built pair by pair, so the
first-row ownership rule and the one-shape-per-relation gate above are
retired, and three of the fifteen cases were corrected to the per-pair rule.

<a id="sel-0053"></a>
### SEL-0053 — Joined rows over records of different shapes: Lisp builds wrong rows and crashes, and the hosts disagree on missing, null and boolean fields

**Resolved 2026-09-23 · P1 · All five · Owner: unassigned.**
Source: SEL-0052's differential oracle, 2026-09-22; present at `0f9031d`.

**Next action:** Lisp's compiled join projector reads each row's storage by
the FIRST row's shape: over `A = LIST(RECORD("id", 1, "bid", 2), RECORD("id",
2, "bid", 2, "cid", 4, "amt", 5), …)` a joined row's `amt` is a whole record
and another row's `status` holds another field's value, and 4–32 programs per
3,000 end in `INVALID-ARRAY-INDEX-ERROR`. Separately the five hosts disagree
on 14–51 of 6,000 programs with uniform shapes and 169–242 with mixed shapes
(C++ and Lisp most often alone; e.g. a missing, null or boolean field read
through a joined row answers `E_NO_KEY` in three hosts, `E_NULL` in C++,
`E_NOT_BIN` in Lisp). Reproduce with `tools/join-filter-oracle/run.sh 3000
52001 mixed`; write the minimal `.selt` cases first, then fix each host's row
building against spec §7.4.

**Close when:** No host crashes and the hosts agree on every program of the
oracle's mixed and uniform corpora.

**History (2026-09-22, the oracle's two fixed corpora replayed on 19
commits from `e4b0432` to the working tree):** not a worklist regression.
C++ and Lisp stand apart on mixed-shape rows from the commit that gave each
host `LINK` (`e4b0432` for Lisp, `8fe0e3a` for the rest); the pattern
settled at `70d532a` (09-16, a cross-host fix), which also made every Lisp
joined row take a record shape while Lisp's compiled projector kept reading
each row's storage by the first row's shape — the crashes date from there.
JS's lone answers on uniform rows doubled at `c5a8991` (09-20, "wip
optimizations"): shaped `RECORD` literals moved JS joins onto its compiled
projector, which drops a null right field the generic row builder promotes —
two code paths of one host disagreeing, found by no test. A Python burst of
109 changed answers at `1614eed` (09-20, "python optimizations") was undone
by `061358b`, the worklist's first commit. Common cause: each host's
row-building fast paths were checked against conformance cases whose rows
all share one shape and hold no nulls.

**Resolution:** Resolved 2026-09-23, working tree on `0f9031d` (commit
pending). *Tests first.* The spec text was the thing missing: §7.4 said how
a joined row is built but not from which elements, and every host had
answered "the first ones". A model of §7.4 written from the text and no
host (`tools/join-rows-oracle/model.py`) settled the reading, and the spec
now states it: the fields a joined row carries are decided for each pair
from its own two elements, in each element's own field order; a *nested
record* is a record with at least one field (a list, BOOL, BIN or NULL is a
scalar field); the lowercase binder key is added only where the element has
none, and an element that already has a field named like its relation is
bound as it is; LINK_LEFT's null record is shaped like the first right
element as bound, just the name keys with no right elements, NULL with no
name. From the model: 21 conformance cases (`16-joined-rows.selt`: per-pair
promotion left and right, field order, a clash on one pair only, case
folding, NULL on either side, nested-or-scalar per element, lists and
booleans as scalars, the null record's shapes, empty and literal sides,
mixed matched and unmatched rows, chains, five-argument binders, the binder
rules), and a generator of mixed-shape `LINK`/`LINK_LEFT` programs checked
against the model (`tools/join-rows-oracle/`, a gate lane at 2,000
programs). Before the fix every host failed some of both. *Fix.* Every host builds
each row by the rule; the compiled fast path keeps a plan per pair of
shapes that carries the only two facts that change a row — whether each
left field is a nested record, whether each right field it takes is a
non-NULL scalar — checks them as it copies, checks the left row once per
left row, and (JS, C++, Lisp) skips the right checks when the join's own
bucketing pass saw every right row flat; in Python and PHP that pass costs
more than the checks it saves, so they check pair by pair. SEL-0052's
first-row ownership rule and its one-shape-per-relation gate are retired —
no row depends on another — and three of its conformance cases, written to
the first-row rule, now say what the per-pair rule answers
(`early.a-left-field-an-element-lacks-raises-on-its-row`,
`passed-over.a-field-both-sides-carry-is-neither-side-s`,
`chain.then-filter.early.a-left-field-reaches-through-two-joins`).
*Designs weighed for speed.* A signature of every field's category per
pair (first cut) cost JS 78% on S6 and Lisp 30%; the two-fact guards with
fused checks brought every host within a few percent; PHP's last few
percent were its cycle collector — a local that lets go of a still-held
Value queues a GC root, and one per field per row made the collector run
often — so PHP reads fields in place. *Scale,* against this tree with the
pre-SEL-0053 row builders (a scratch copy), interleaved, seven runs after
two warm-ups, medians: S1 C++ +1.1%, JS +2.5%, PHP +3.5%, Python +2.9%,
Lisp 0; S6 C++ +0.4%, JS +1.5%, PHP +0.9%, Python +4.8% (its guards are
generated code, and the right-row pass does not pay there; SEL-0056), Lisp −2.8%;
S2–S4 within spread; S5 faster everywhere (C++ −13%, JS −6%, PHP −30%,
Lisp −16%, Python −1%), since the retired gates had held the pre-filter
back. *Validation:* 980 conformance cases per host; pytest 635 (one caught a
reference cycle in the first cut's generated Python), JS optimizer 142, PHP
optimizer 139, C++ unit 173 and SQL unit 85, Lisp 553, SQL cases 889;
`make asan` clean; the joined-row oracle 0 wrong on seven roster entries
over 14 seeds × 3,000 programs; the join-filter oracle 0 cross-host
disagreements on both corpora and no crash, its as-written-vs-helper count
(SEL-0054) unchanged at 63 per host; `tools/check.sh` ALL GREEN on seven roster entries (598 s) with the gate's Docker servers, the new joined-rows lane among its layers, 197 mutations caught.

<a id="sel-0054"></a>
### SEL-0054 — The physical join-predicate pushdown changes values: renumbered keys, lost join-key errors, relation names under explicit binders, first-row promotion

**Proposed · P2 · All five · Owner: unassigned.**
Source: SEL-0052's differential oracle, 2026-09-22; present at `0f9031d`, every host alike.

**Next action:** On 49–84 of 3,000 join-then-filter programs every host
answers differently from the same program through helper variables, and the
cases trace to the physical pushdown (SEL-0051's tentative FILTERs), not to
the run-time pre-filter: a FILTER pushed before a join renumbers the joined
rows, so a FILTER whose keys are observed answers `"1"` where the program
says `"2"`; a FILTER pushed between two joins drops rows before the upper
join computes its key, losing that key's `E_NO_KEY`; under explicit binders
(`LINK(C, L, R, …)`) `_["C"]` is no member of the joined row, yet the
optimiser takes the relation's name for the right side and pushes the read
into C; and a drop before a join changes which row is first, and so what the
join promotes. The rules SEL-0052 gave the run-time pre-filter (upper-key
obligations, first-row ownership, uniform shapes) are what the pushdown lacks
— but the tree cannot check them. Decide: restrict the pushdown to what is
provable from the tree (keys unobserved, no join above, binder names not
relation names), or retire it in favour of the run-time pre-filter, which
now recovers S5 on its own. Measure the scale set either way.

**Close when:** No host disagrees with its own helper form on the oracle's
corpora, beyond SEL-0053's cases.

**History (2026-09-22, the same replay):** there from the start, and the
same in every host. The join-predicate pushdown arrived with `LINK` itself
(`e4b0432` in Lisp, ported to the other four at `8fe0e3a`), faithfully
including its unsoundness: at introduction 1,017–1,058 of 3,000 programs
per host answered differently from their helper form, 111–127 of them in
this item's classes (an error lost, a value or keys changed) and the rest the
SEL-0051 class (a pushed conjunct raising where the program would not). The
counts stayed flat through the optimisation rounds and the worklist until
SEL-0051 (`0f9031d`) removed its class, leaving 49–66; SEL-0052 left them
unchanged. The one worklist-era regression in this measure was Python's own:
SEL-0049's run-time pre-filter (committed in `b274aa2`) lost errors on 4–21
more programs, SEL-0050 kept that, and SEL-0052 removed it. Cause: the
rewrite was written as a performance transformation and checked by
conformance cases and cross-host fuzzing, both of which every host passes
while making the same mistake; nothing compared a program with its own
unoptimised form until this oracle.

**Resolution:** Pending.

<a id="sel-0055"></a>
### SEL-0055 — The scale harness's database lane expects SQL for scenario 6, which the planner keeps in memory

**Proposed · P3 · Validation · Owner: unassigned.**
Source: SEL-0052's real-database run, 2026-09-22; present at `0f9031d`.

**Next action:** `tools/scale-test/run_benchmarks.py` refuses to start —
"postgresql has no SQL statement for scenario6" — because
`benchmark_results.json` still records S6 as pushed down while every host's
planner classifies it `pure_memory`. Either the reference or the planner is
out of date; decide which, regenerate the reference if it is the former, and
run the lane over all six scenarios.

**Close when:** The database lane runs S1–S6 against PostgreSQL and MariaDB
without `--only`.

**Resolution:** Pending.

<a id="sel-0056"></a>
### SEL-0056 — Python's per-pair joined rows cost scenario 6 about 5%

**Proposed · P3 · Python · Owner: unassigned.**
Source: SEL-0053 closure, 2026-09-23; present at `b791679`.

**Next action:** SEL-0053 made every host build each joined row from its own
pair, and Python kept the largest residual: S6 800.0 → 838.4 ms median
(+4.8%, min 799 → 836), S1 +2.9%, against the same tree with the
pre-SEL-0053 row builders. S6 is `PRODUCTS .> LINK_LEFT(ORDER_ITEMS, …)`,
one-to-many, about 180,000 pairs, and every pair runs the generated plan's
right-field guards (`r_s[i].kind != "NONE" or r_s[i].is_list` per promoted
field); the left guards run once per left row and the plan is found through
the last-pair memo, so the guards are what is left. What was tried: the pass
that recovered S6 in JS, C++ and Lisp — learn while bucketing that every
right row is flat and skip the right guards — made Python worse (S6 +10%,
S1 +7.6%, S5 +7%), since a Python-level loop over each right row's fields
costs more than the generated guards it saves; it was removed. In JS the cost
was proved (by ablation) to be reading right values scattered in bucket
order; in Python that is unverified — profile first. Candidates: the
flatness test as generated code per right shape, fused into the bucketing
loop (one expression, no `zip`, no set lookups); a flatness fact recorded
where rows are made (`Value.from_native` rows, cleared by any in-place
store), which touches `value.py` and needs every mutation path audited; or
accepting the cost with the measurement recorded. Baseline: SEL-0052 and
SEL-0053 landed together in `b791679`, so no commit holds the tree those
figures were measured against (SEL-0052's pre-filter with the old row
builders). Measure in a scratch worktree at `b791679` with `0f9031d`'s
first-row projector restored in `python/sel/builtins/structure.py` (S1 and S6
only; S5 moves with the pre-filter), or against `0f9031d` whole for S6, which
the pre-filter does not touch — never by swapping files in the shared
checkout.

**Close when:** Python S6 and S1 are within run-to-run spread of that
baseline (interleaved, seven runs after two warm-ups) with the joined-row
oracle 0 wrong, or the cost is accepted with the profile that explains it.

**Resolution:** Pending.

## Suggested order

Start with SEL-0001–0004 and revalidate SEL-0034. Take verified cleanup items
SEL-0005–0014 in small compatible changes. Evaluate the optional factoring work
separately from correctness fixes. Begin generation with SEL-0020 after resolving
arity drift, then add SEL-0025/0026 and progress to binding/math metadata.
Keep performance investigations and the full integration checkpoint explicit;
a measured decision to retain an intentional duplication/tradeoff is a valid
closure when its evidence is recorded.

## Import coverage

| Historical scan label / observation | Working IDs |
| --- | --- |
| Cross-lane arity | SEL-0002 |
| PY-1 / PY-2 / PY-3 | SEL-0005 / SEL-0009–0010 / SEL-0015 |
| JS-1 / JS-2 | SEL-0006 / SEL-0016 |
| PHP-1 / PHP-2 | SEL-0004 / SEL-0007, SEL-0014 |
| CL-1 / CL-2 / traversal observation | SEL-0008 / SEL-0011 / SEL-0017 |
| CPP-1 / CPP-2 | SEL-0012–0013 / SEL-0018 |
| Remaining raw candidates | SEL-0019 |
| Generation roadmap and gates | SEL-0020–0026, SEL-0033 |
| Surrounding correctness/performance follow-ups | SEL-0001, SEL-0003, SEL-0027–0032 |
| Earlier SQL boundaries / history reconciliation | SEL-0034–0037 |
| Database fuzz lane findings, 2026-09-21 | SEL-0034 (closure), SEL-0041 |
| Review reconciliation, 2026-09-22 | SEL-0037 (closure), SEL-0042–0047 |
| SQL fuzz lane in SQL mode, 2026-09-22 | SEL-0048 |
| API-lane analysis, 2026-09-22 | SEL-0044, SEL-0049, SEL-0050 (closures), SEL-0051 |
| SEL-0051 closure, 2026-09-22 | SEL-0052 |
| SEL-0052's differential oracle and real-database run, 2026-09-22 | SEL-0053, SEL-0054, SEL-0055 |
| SEL-0053 closure, 2026-09-23 | SEL-0056 |
