# The SEL→SQL layer Lisp still owes

**This document has a finite life.** It is deleted in the commit that finishes
the work it describes. If you are reading it after that, it should not exist.

The file is still called `PARSER-MIGRATION.md` because converting the parsers is
what it started as, and half a dozen source comments point at the name. **That
part is finished.** All five hosts are precedence climbing, all five count the
index bracket, and `docs/EXTENDING.md` now describes the shared shape as the
design it is rather than as a migration in progress. The parked conformance
cases went into `conformance/10-limits.selt` with Lisp's turn, and `spec/SPEC.md`
§6.4 gained the sentence about what each nesting construct costs.

What is left is one deliverable in one host. **C++ has landed** — 383 `.sqlt`
cases, the 2000×4 translator fuzz against js/php/python with 0 disagreements,
the map replay with 0 differences, and five mutations of its own. What it
decided is below, because most of it is Lisp's decision too.

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
  each one function plus one line in that file's `OUTPUTS`. C++ needed a third,
  for the replay data, and put its shared rendering in `tools/cpp-emit.mjs`
  rather than copying it into both generators. **The other hosts'
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

`sql/cases/12-aggregates.sqlt`'s `agg.clist.mixed-keys-keep-insertion-order`
passes on C++, which used `std::vector` of pairs; an alist is the Lisp shape.

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
still catches it. Both were written because the JS port needed them; C++ passes
both, and Lisp is what is left.

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

### What C++ settled, that Lisp does not have to settle again

**The map is generated SOURCE, not data.** No host reads a file at run time.
`tools/gen-sql-map.mjs` renders `sql/dialects/*.json` into each host's own
language, and `tools/gen-sql-cases.mjs` does the same for the `--- bindings` and
`--- register` blocks — as **typed constructor calls**, so the runner parses
nothing either. For Lisp that is easy: it ships source anyway, so the emitted
form is a `defparameter` and a sequence of `map:define` calls.

**A case that cannot be expressed is not skipped.** Twelve `.sqlt` cases pass a
malformed binding on purpose — a JSON array where a name belongs, a section that
is not one of the three. C++'s typed constructors cannot be handed one, so its
runner reports them as refused by the type system and the total stays 383. If
Lisp's constructors are equally strict, do the same; if they are not, it simply
runs them.

**`entry` is two-phase: the whole overlay chain, then the whole shipped chain.**
The generated tables are pre-flattened, so consulting them per level lets a
leaf's inherited entry shadow one registered against a base — and registering
against `ansi` is documented to reach everything below it. The map replay cannot
see this (it registers everything as overlay); `register.define.on-a-base-reaches-every-leaf`
can.

**Frame writes are assign, not insert-if-absent.** An aggregate binder literally
named `_K` must lose to the later `_K` write, because that is what the evaluator
does.

**Stage 1's `clist` is shared, not copied.** `R[1] = 1; A = R; R[2] = 2;
COUNT(A)` is 2 — `A` holds the same list `R` does, and copying on append answers
1.

**Keys are the parser's own characters.** It normalises leading zeros and
nothing else, so `R[1.0]` and `R[1]` are two different keys and SEL agrees.
Canonicalising collapses them.

**Two rules of the shared corpus were unasserted until C++ mutated itself**, and
both are now cases every host runs: a BIN slot must be *inlined* rather than
bound (visible only in params mode with an empty params list), and escaping
applies the *longest* rule first (visible only through a registered dialect,
since no shipped one has a multi-character escape key).

**Read the reference host with help.** The C++ port was written against a
subsystem-by-subsystem map of `python/sel/sql/translator.py`, each section then
re-checked against the source by a second reader. All seven sections came back
with corrections, and one of them was a live bug in already-written code.

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

When Lisp is finished — it is the last host — delete this file, and drop its
line from `README.md` and the reference to it in `docs/EXTENDING.md`.

