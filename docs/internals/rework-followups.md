# Rework follow-ups: evidence and proposed resolution

Investigation: 2026-10-06. This is a proposal, not an implemented change.
The checkout's tracker (`docs/interim/2026-10-05-consolidated-review/resolution-worklist.md`, final reconciliation and addendum) records the completed gate but does not contain the two quoted follow-up bullets. The findings below come from current source and fresh local tests.

## 1. Extend right-side rejection to left joins

S6, from `tools/scale-test/benchmark_results.json`, begins:

```sel
PRODUCTS
  .> LINK_LEFT(ORDER_ITEMS, _['products']['id'] == _2['product_id'])
  .> FILTER(IS_NULL(_['order_items']['id']))
```

It then maps the unsold products to category/status, deduplicates, sorts, and takes ten results. This is an anti-join-like workload, but not an unconditional anti-join: a real matched item with a NULL id also passes the predicate. A missing id can raise rather than mean NULL.

The existing runtime pre-filter already rejects right rows for inner LINK. Every host explicitly excludes LINK_LEFT from this path. Python's guard is in `_settle_prefix`, `python/sel/builtins/structure.py`. The existing mechanism keeps rejected right rows in their hash buckets, computes all join keys, and skips their projection while preserving the original row positions. It is not a rewrite of the physical AST.

### Fresh evidence

On `dataset-10x.json`, instrumenting Python's `make_join_projector` counted **90,000 matched rows and 100 unmatched rows**. The complete S6 result matched the committed reference. All 90,000 matched rows fail the S6 predicate on this fixture; the 100 unmatched rows feed the remaining pipeline.

A fixture-only experiment reused `_probe_left_rows` with a rejection set containing right rows whose `id` was non-NULL. Buckets, join-key evaluation, unmatched projection and final FILTER were unchanged. Eight runs in ABBAABBA order, four per variant, all reference-equal:

| Python execution | Median |
|---|---:|
| Current S6 | 711.49 ms |
| Skip matched projections | 404.56 ms |

That is **1.76×**, or 43% less elapsed time on this run. The experiment is not a general optimizer and is not a cross-host speed guarantee.

A separate cProfile execution took 2.312 seconds under profiling: `_bucket_right_rows` accounted for 0.888 seconds cumulatively and `_probe_left_rows` 0.304 seconds. Thus this measurement does **not** establish that 80% of current runtime is row construction. Almost all constructed joined rows are discarded; that is a different claim. Bucketing and right-side alias construction remain substantial costs. Do not add nested cumulative profile times together.

### Recommended initial scope

Implement a runtime rejection path for an equi-LINK_LEFT directly underneath FILTER, initially recognizing only a leading `IS_NULL` of a literal field through the unambiguous right binder. Prove the field access safe across all right rows, with the same alias semantics the projector uses. Keep FILTER running on emitted rows, including null-extended rows. Decline optimization on missing fields, binder collisions, unsupported expressions, or filters handed through an upper join. Broaden only after this version passes the oracle.

The essential behavior is already expressible with Python's probe loop. The following is an executable-shaped excerpt of that loop, not a complete patch; `matches` must be the original bucket, and `position` is the original joined-row ordinal:

```python
if matches:
    for right in matches:
        check_collection(position, args.pos)  # preserve the logical join limit
        if id(right) in rejected:
            dropped = True
            position += 1
            continue
        output.append(project(row, right))
        if keys is not None:
            keys.append(str(position))
        position += 1
elif left_join:
    check_collection(position, args.pos)
    output.append(project(row, None))
    if keys is not None:
        keys.append(str(position))
    position += 1
```

Crucially, a bucket whose matches all fail the FILTER is still a **matched** bucket. It must not produce a replacement null row. Genuine unmatched rows still go through the final FILTER. Do not mark the new right predicate globally “already applied”: that would incorrectly skip it on null-extended rows. Keep numbering enabled when observable; count skipped logical rows toward collection limits. All existing join-key validation must run before rejecting projections.

For the first version, a new right rejection must not permit later conjuncts to move ahead of it. Either stop stage analysis at the recognized predicate or represent it as a distinct rejection proof. Do not simply remove `not call.left_join` from the existing guard: its applied-conjunct reporting was designed for inner joins.

### Files and acceptance

1. Document the preservation requirements in `spec/SPEC.md` §7.4 and `docs/contributing.md`; no user-visible syntax or semantic change is intended.
2. Add shared cases to `conformance/15-relational.selt` and `16-joined-rows.selt`: all matches rejected; unmatched row passing/failing the predicate; real matched NULL id; missing id; duplicate matches; `_K`/INDEXES gaps; empty inputs; explicit and colliding binders; join-key errors; earlier/later erroring conjuncts; filters between chained joins. Add a collection-limit boundary test in host tests.
3. Extend the runtime machinery at the following sites:

| Host | Main join files |
|---|---|
| Python | `python/sel/builtins/structure.py`: `_settle_prefix`, `_reject_right_rows`, `_probe_left_rows`; FILTER reporting in `builtins/aggregate.py` |
| JS | `js/src/builtins/structure.mjs`: stage walk, prefix settlement, right rejection and left probe |
| PHP | `php/src/Builtins/Structure.php`: `stageWalk` and join's `rightHere`/projection loop |
| C++ | `cpp/sel.cpp`: `join_stage_walk`, `do_link`, right rejection/reporting |
| Lisp | `lisp/src/builtins/structure.lisp`: `join-stage-walk`, `right-here`, join report/projection loop |
| Go | `go/sel/builtins_aggregate.go` and `go/sel/join_prefilter.go` |
| Rust | `rust/src/builtins/structure.rs` and `rust/src/join_prefilter.rs` |

All seven implementations have the inner-only guard and the same fundamental bucket/match distinction, so the algorithm applies to them. Python object identities in the rejection set translate to each implementation's existing right-row identity/index representation, not necessarily pointer identities.

4. Extend `tools/join-filter-oracle/gen.py` with left-join right predicates. Compare direct pipelines to helper-variable forms, including error locations. Run `tools/join-filter-oracle/run.sh`, join-row oracle, shared conformance and then `tools/check.sh`.
5. Measure S6 and S1–S5 on every host with `tools/commit-benchmark/snapshot.py` using identical fixtures, warmups and interleaved runs. Require identical results and no material regression. A day is a plausible prototype estimate, not a proven all-host implementation-and-gate estimate.

Existing coverage was rerun: `15-relational.selt` + `16-joined-rows.selt`: **188 passed per host, all seven hosts**. These establish today's preservation requirements; they do not test a future optimization.

## 2. Give non-shipped define() callbacks one effects rule

The relevant distinction is **whether the optimizer may assume a call cannot mutate values**. It need not change how callbacks are invoked, replaced, or registered with SQL.

Python's `registry.is_host_function` returns true for explicitly registered host functions **or any name absent from BUILTIN_MANIFEST**. PHP and JS's equivalent helpers only check the public registration set. C++ distinguishes `Spec::host` from `Spec::fn`; Go keeps a public host registration set; Rust has native versus host callback variants. These representations must not be mistaken for proof that an arbitrary internal callback is harmless.

Lisp is already conservative in its effects analysis: `shipped-call-p` reads `spec-shipped`, set when the library seals its shipped builtins. A subsequently added function is not shipped. PHP also already uses manifest membership in `Structure::pureSource`, while its write analysis uses `Registry::isHostFunction`: two consumers in the same host currently answer differently.

### Observable proof, beyond classification

The Python test `test_a_function_defined_through_the_registry_brings_the_copies_back` in `python/tests/test_value_ownership.py` registers a callback that changes the context's first row from `k=1` to `k=9` and returns index `1`. It evaluates:

```sel
FILTER(X, TRUE)[T_POKE()]["k"]
```

FILTER collected the row before T_POKE ran. With required copy semantics the answer is **1**, although X is subsequently mutated. Fresh Python tests pass. An equivalent fresh PHP `Registry::define` probe returns **9**: write-free copy elision lets the later mutation reach the collected row. Direct registration-classification probes also report Python=true, PHP=false, JS=false. This investigation did not execute that mutation probe on the other hosts; their relevant representations were inspected in source.

Existing coverage also includes:

- `python/tests/test_perf_regression.py::test_filter_application_defined_function_installed_through_lower_level_api`: disables FILTER item adoption.
- `python/tests/test_perf_hybrid_context.py::test_application_function_uses_lower_level_definition_api`: mutation through a low-level callback must not escape into the caller's hybrid context.

Fresh run of these three complete Python test files: **129 passed** using `.venv/bin/python` (system Python has no pytest).

### Recommended policy and representative change

**Only shipped functions may receive the builtin effects assumption. Every non-shipped callback is potentially effectful, regardless of registration API.** Keep public registration/replacement and SQL arity rules separate. Choosing the other policy would require a documented trusted-extension contract and would abandon existing Python mutation guarantees; a one-line Python speed tweak is not an adequate resolution.

For PHP, the direct counterpart of the existing Python rule is:

```php
public static function isHostFunction(string $name): bool
{
    $key = Utf8::upper($name);
    return isset(self::$host[$key])
        || !isset(BuiltinManifest::BUILTINS[$key]);
}
```

This uses existing classes and fields in `php/src/Registry.php`. Update its docblock to say that this is an effects classification. Leave `hostArity`, the replacement checks, and callback dispatch unchanged. Longer term, a name such as `mayHaveApplicationEffects` would express the purpose more accurately.

Resolution sequence:

1. Pin the distinction in `spec/SPEC.md` §8.1 / `spec/builtins.md` and contributor guidance: internal definition is not a purity declaration.
2. Add native registration tests in every host, using the FILTER mutation probe and hybrid-context isolation probe. These need host harnesses: `.selt` cannot install a native callback on its own. Cover public registration, internal definition, a normal shipped builtin, replacement, and lazy/binding extension functions where supported.
3. Update Python registry documentation; apply the classifier rule in `php/src/Registry.php` and `js/src/registry.mjs`. Audit all copy-elision, join-source purity, and hybrid-context consumers.
4. In C++ (`cpp/sel_ast.hpp`, `cpp/sel.cpp`, `cpp/sel_sql_hybrid.cpp`), Go (`go/sel/registry.go` and analysis consumers), and Rust (`rust/src/builtins/mod.rs` and analysis consumers), add or derive a separate effects classification. Do **not** convert native callback storage into host callback storage: their invocation signatures and registration lifecycles differ. Keep Lisp's `spec-shipped` rule and test post-startup low-level definitions.
5. Audit `tools/scale-test/sel_benchmarks.*` and extension examples: several benchmark callbacks use low-level definitions. Conservative effects handling can restore copies and cost time. Measure that cost openly; do not whitelist benchmarks as pure just to recover a score.
6. Run registration/API, ownership, optimizer and hybrid tests, fragment checks, all-host benchmarks and the full gate. Only then consider an explicit, separately specified effects declaration for trusted extensions if measurements justify it.

Do the effects-contract fix first; then base the left-join optimization's purity checks on that common rule. No production code was changed by this investigation.
