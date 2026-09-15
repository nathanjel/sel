# NEW. The FILTER-before-MAP rewrite renumbers the result's keys: `_K` after it, and the keys of the answer, differ from the unoptimised program

**Status:** OPEN, found 2026-09-15 while fixing findings AJ and R (not verified by a second agent; one witness per host below). Not fixed: it is a rule of the logical optimiser, in all five hosts, and needs a decision on the guard.

**Verdict:** reproduced on js, cpp, python (the same optimiser rule exists in php and lisp) · **severity:** high (a value differs from the spec's, in every host, so the fuzzer cannot see it) · **introduced:** pre-existing

## Summary

`FILTER` is the one aggregate that preserves its input's keys (spec §7.3); `MAP` renumbers. The logical optimiser moves a `FILTER` in front of a `MAP` whose projection passes the filtered fields through, and every host's `run()` evaluates that rewrite. After the swap the MAP renumbers the *filtered* rows, so the answer's keys are `1..n` where the program as written keeps the source keys, and a later `_K` read sees the renumbered ones. The same pipeline written through a helper assignment is not rewritten (the rule does not cross an assignment) and answers as the spec says, so a program's keys depend on whether it was written in one expression or two.

## Witness

```
LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("id", _["id"])) .> FILTER(_["id"] > 1)
  js / cpp / py:  -{"1"=-{"id"=t"2"}}          (rewritten: FILTER, then MAP renumbers)

X = LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("id", _["id"])); X .> FILTER(_["id"] > 1)
  js / cpp / py:  -{"2"=-{"id"=t"2"}}          (as written: MAP, then FILTER keeps key "2")

LIST(RECORD("id", 1), RECORD("id", 2)) .> MAP(RECORD("id", _["id"])) .> FILTER(_["id"] > 1) .> MAP(_K)
  js / cpp / py:  -{"1"=t"1"}                  (the spec's answer is t"2")
```

The second form is the unoptimised semantics: `=` copies, and the optimiser does not look through the assignment. The first two are the same pipeline.

## Where

The MAP/FILTER swap in each host's logical optimiser (`logicalSteps` / `logical_steps` / `logical-step-pair`): its guards (`readsRowOrKey`, `stepReadsKey`, the pass-through field set — docs/EXTENDING.md "Values") check what the *moved* FILTER reads, not what a later step can observe of the result's keys. A swap is only value-preserving when either the FILTER drops nothing (unknowable) or nothing downstream observes keys — and the final answer's keys are always observable (`tree` dumps them, and so does indexing by key).

## Suggested fix — case first

Add a `.selt` case for the third witness (`rel.map.then-filter-keeps-source-keys-through-map-of-key`, expect `tree -{"1"=t"2"}`) and one for the first (`tree -{"2"=-{"id"=t"2"}}`) before touching any host. Then either drop the FILTER-before-MAP rule from `run()`'s physical tree (keep it for the SQL planner, where SQL has no keys and the translator renders both orders the same), or make the swap re-key: it is exact only if the MAP after the swap keeps the keys FILTER kept, which no MAP does today. The planner's own swap is unaffected in the SQL lane (rows have no keys there), but the continuation runs `run()`'s tree, so `hybrid` plans answer the rewritten keys too.
