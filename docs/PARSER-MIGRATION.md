# The SEL→SQL layer C++ and Lisp still owe

**This document has a finite life.** It is deleted in the commit that finishes
the work it describes. If you are reading it after that, it should not exist.

The file is still called `PARSER-MIGRATION.md` because converting the parsers is
what it started as, and half a dozen source comments point at the name. **That
part is finished.** All five hosts are precedence climbing, all five count the
index bracket, and `docs/EXTENDING.md` now describes the shared shape as the
design it is rather than as a migration in progress. The parked conformance
cases went into `conformance/10-limits.selt` with Lisp's turn, and `spec/SPEC.md`
§6.4 gained the sentence about what each nesting construct costs.

What is left is one deliverable in two hosts.

- [What is left](#what-is-left)
- [What the JS port learned that generalises](#what-the-js-port-learned-that-generalises)
- [Per host](#per-host)
- [How to do one](#how-to-do-one)

---

## What is left

**The SEL→SQL translator, in C++ and in Lisp.** `docs/SQL-TRANSLATION.md` is
normative; §14 records what each previous port cost and why. In outline, per
host:
- an emitter in `tools/gen-sql-map.mjs` and one in `tools/gen-sql-cases.mjs`,
  each one function plus one line in that file's `OUTPUTS`. **The other hosts'
  generated files must regenerate byte-identical** — that is the check that you
  added a host rather than changed shared data.
- the layer itself, transcribed from `python/sel/sql/` (the JS port used Python
  rather than the PHP original, and 368 of 368 passed on the first full run).
- a `sqlt` runner, transcribed from `python/bin/sqlt`; the three host checks, not
  PHP's five. `oracle` stays PHP-only — §14, M7 says why — and so does `sqldoc`,
  which checks the design document against the cases it quotes and is therefore
  a property of that document rather than of any host.
- wiring in `tools/impls.sh` (`impl_sql`) and a check in `tools/mutate-sql.py`.
  A check that cannot reach the code under test cannot measure it, so the mutation
  harness needs the new runner before any mutation of the new layer means anything.
- mutations in `sql/mutations.json` for whatever the host had to decide for
  itself. Every host so far has had at least one such decision.

**What the JS port learned that generalises.** Most of its host-shaped decisions
were about JS objects and do not transfer. One does, and it is the one that
changes emitted bytes:

> **An aggregate's elements must live in an insertion-ordered container.** A
> `clist` built by indexed assignment may mix word keys with the positional keys
> `"1"`, `"2"`, … and it must unroll in the order the assignments were written.
> A JS object reorders integer-like keys to the front; **C++'s `std::map` sorts
> every key**, and a Lisp hash table has no order at all. `std::vector` of pairs
> and an alist are the shapes that work.

`conformance/`'s sibling case `agg.clist.mixed-keys-keep-insertion-order` in
`sql/cases/12-aggregates.sqlt` pins this for every host, and
`js-clist-order-through-a-plain-object` in `sql/mutations.json` proves the case
still catches it. Both were written because the JS port needed them; both are
waiting for C++ and Lisp.

**What the JS review found that generalises.** The four-lens review of the JS
layer confirmed six findings; one was a shipped defect and its lesson is not
about JS:

> **Template substitution must be LITERAL and GLOBAL, and you must check that
> your host's string replace is both.** Python's `str.replace` and PHP's
> `str_replace` are; JS's `String.prototype.replace(string, string)` is neither
> — it rewrites only the first occurrence, and it interprets `$$`, `$&`,
> `` $` ``, `$'` and `$1`-`$9` inside the *replacement*. The replacement is
> rendered SQL carrying identifiers the application chose, so a column named
> ``a$'b`` spliced the template's own tail back into the output and produced a
> query asking about a different column. `replaceAll` does not fix it.
>
> C++ has no string replace at all, so this arrives as "write the loop"; Lisp's
> `cl-ppcre:regex-replace-all` treats its replacement as a template where `\1`
> and `\&` are directives, which is the same trap in a different spelling —
> `search`/`replace` on subseqs, or `:simple-calls`, is the shape that works.

`review.slot.dollar-pattern-in-an-identifier`, `review.slot.repeated-in-a-skeleton`
and `review.slot.repeated-in-a-numeric-wrap` in `sql/cases/13-review.sqlt` pin
all three sites for every host, and `js-slot-replace-is-not-literal` in
`sql/mutations.json` proves they still catch it.

The review's other survivors turned out to be cross-host too, and all four are
now fixed in JS, PHP and Python. **A new host must arrive with them already
right**, because the cases and mutations that pin them are shared:

- **A lexical entry may not expand into itself.** `Emit.fill` expands one lexical
  key inside another, and nothing stopped a registered
  `{textCast: 'X({textCast:0})'}` recursing until the host died — RangeError on
  JS, RecursionError on Python, a host crash through the public API either way.
  Track the keys being expanded and refuse a repeat; do NOT cap a depth, because
  with cycles refused the chain is bounded by the fifteen lexical keys and a cap
  would need a number nobody can justify.
- **A kind-mismatch refusal must carry a position.** `unify()` took a position
  and every caller passed none, so `IF(TRUE, TRUE, "A-1")` reported
  `E_SQL_SHAPE at 0:0` in all three hosts while every other refusal in the layer
  reported where it happened.
- **An empty `identQuote` or `textQuote` must be refused at registration.** A
  quote character that is not a character cannot quote, and the hosts each
  produced a different nonsense from it. `textCollate` is legitimately empty —
  `ansi` and `sqlite` ship it that way — so refuse those two keys, not every key.
- **Dead shared data.** `hasBindings` was emitted into every generated case by
  all three emitters and read by none; `Bindings.KINDS` was defined in all three
  hosts and read by none (`Fragment`'s `KINDS` is the one that is used). Both are
  gone. A new host should not add them back.

`review.lexical.expands-into-itself`, `review.unify.refusal-carries-a-position`
and `review.lexical.empty-quote-is-refused` in `sql/cases/13-review.sqlt` pin the
first three for every host, and four mutations in `sql/mutations.json` prove the
cases still catch them.
---

## Per host

### C++

`php/src/Sql/` is 6,381 lines and `python/sel/sql/` is 5,459, and C++ will land
more than either. **Not a transcription, and this is the decision to make
first.** The obvious shape for the generated dialect map is a JSON blob parsed at
load, because the map is heterogeneous nested data and the runtime `define()`
API takes application-supplied data of the same shape — which is also what
`options`, the `--- register` case data and the binding specs are. That is ruled
out: **this host is not taking a JSON reader.** The representation is an open
question and it is the question to answer before any of the layer is written.

Whatever answers it, the container warning above is aimed squarely at it:
`std::map` sorts its keys, and an aggregate's elements must unroll in the order
they were assigned.

The AST-header problem this section used to name is **done**. `cpp/sel_ast.hpp`
holds `NT`, `Node`, `NodePtr` and `Spec`; `Spec`, `Context`, `Args` and
`eval_node` are at `sel::` scope; `Program::ast()` hands the tree out. A second
translation unit can walk it, and `cpp/bin/ast.cpp` is one, so the header cannot
quietly stop being includable.

### Lisp

Transcribe from `python/sel/sql/`. A hash table has no iteration order, so an
aggregate's elements want an alist; see the container warning above.

`cl-ppcre:regex-replace-all` treats its replacement as a template where `\1` and
`\&` are directives, which is the template-substitution trap in a different
spelling — `search`/`replace` on subseqs, or `:simple-calls`, is the shape that
works.

---

## How to do one

One host per release, so a regression is attributable to one commit.

1. Write the emitters first and regenerate. **The other hosts' generated files
   must come out byte-identical** — that is the check that you added a host
   rather than changed shared data.
2. `tools/check.sh`, which runs `sqlt` for every host that has one.
3. `tools/stress.sh`, if you touched anything that walks a `Node` or a `Value`.
4. `python3 tools/mutate-sql.py`, which is what says the new cases can fail.
5. If something diverges, add the minimal case to `sql/cases/` **before** fixing
   any host.

When the last host is finished, delete this file, and drop its line from
`README.md` and the reference to it in `docs/EXTENDING.md`.

