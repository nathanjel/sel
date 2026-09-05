# Why the SQL layer kept shipping wrong answers, and what would stop it

Written after M1–M3, when a code review found eight defects — five of them wrong
answers — in code whose 162-case suite was green. This is not a retrospective for
its own sake: the same five classes recurred at every milestone, and each has a
mechanical fix that the project's existing harness is most of the way to already.

---

## 1. The diagnosis, in one sentence

**The SQL layer was built with assertion tests where the rest of the project uses
differential tests, and every defect that survived lived in the gap between the
two.**

Everything else in this repository is checked against something that is not the
author's opinion. Six implementations are diffed against each other. The decimal
core is diffed against a Python oracle. A fuzzer generates programs nobody wrote
and demands unanimity. `check-docs.sh` runs the documentation.

`sql/cases/*.sqlt` does none of that. It asserts that the translator emits a
string **the author chose**. A case is green when the code agrees with what its
author believed, and every defect below is a case where the author believed
something false. The suite could not have caught any of them, because the suite
*is* the belief.

---

## 2. The numbers

Twelve wrong-answer defects reached a commit across three milestones.

| Found by | Count | What found it |
|---|---|---|
| Probing MariaDB while authoring M1 | 4 | asking the server instead of the manual |
| Writing M3's contract examples down | 4 | working an example end to end on paper |
| Code review of M1–M3 | 8 | reading the code against the documents |
| The `.sqlt` suite | **1** | a case written from a document sentence |

That last row is worth more than the other three, because of *which* case it was.
`register.dialect.may-override-a-lexical-key` failed on first run and exposed
that the generator was pre-expanding lexical references, which made runtime
`textCollate` overrides silently dead. It found a real defect nobody suspected.

It could, because it was written from a **sentence in a document** — "a runtime
dialect may override a lexical key" — rather than from the code. Every other case
in the suite was written by reading an implementation and recording what it did,
and a case written that way is a transcription: it cannot fail on the day it is
written, and it cannot disagree with the model that produced it.

So the finding is sharper than "assertion tests are weak". It is:

> A case written from the code confirms the author's model.
> A case written from a document tests it.

The `+` array trap in `Bindings` illustrates the other half. The suite did not
miss it — the suite did not *have* it. `sql/MAP.md` says field names are
case-insensitive; no case said so; nothing failed. One sentence, one missing
case, one silent defect for a milestone.

### What happened when the oracle was actually built

The fix proposed in §3 was built. On its **first two runs**, against code that
had passed three rounds of review and a green 180-case suite, it found five more:

| Defect | Shape |
|---|---|
| `PADL`/`PADR` truncated | `LPAD('7777', 2, '0')` is `'77'`; SEL returns `"7777"` |
| `MIN(1)`/`MAX(1)` emitted invalid SQL | `LEAST` and `GREATEST` need two arguments |
| `params` mode compared numbers as text | `(2.50 = 2.5)` is TRUE, `(? = ?)` over two string parameters is FALSE |
| `x IN <multi-field relation>` | the server selected order 1, SEL selected nothing |
| the row fixture loaded nothing | and reported nine disagreements over zero rows |

Two of those deserve a second look.

The **params-mode number** defect is the §4 argument made literal. The same
expression means two different things in the two render modes, because a bound
parameter arrives as a string in every driver — an exact decimal has no numeric
binding to arrive as. `inline` was right, `params` was wrong, and the 23
params-mode comparisons that "passed" in M2 passed under emulated prepares, which
inline client-side. Nothing but a native prepare against a real server can see it.

The **`IN` over a relation** defect was not an implementation slip. §7.6 of the
design document used a multi-field relation in its own worked example, and no SEL
context makes that translation true: a relation with several fields is a list of
rows, and SEL compares a scalar against a row structurally. It was documented,
implemented, case-tested and wrong, and the only thing that could tell was asking
both sides the same question. That is the entire thesis of this document arriving
in one defect.

So the honest total is **seventeen**, and the row that matters is unchanged:
the suite that asserts what the author believed found one of them.

---

## 3. Class A — the map claims a semantic equivalence that nothing checks

**Eight defects.** The largest class by some margin.

### Example

```jsonc
"CHAR": { "tpl": "CHAR({0} USING utf8mb4)", "ret": "TEXT" }
```

Authored from the manual, reviewed, plausible. `CHAR(n USING cs)` reads `n` as a
**byte sequence**, not a code point, so `CHAR(233 USING utf8mb4)` is `NULL` where
SEL says `é`. Below 128 it agrees, which is worse than disagreeing.

The same shape, seven more times: `TO_BASE64` wraps at 76 characters;
MariaDB's `REGEXP` is case-insensitive by default; its `.` does not match a
newline; its `\d` matches Arabic-Indic digits; `IN` compares under the column
collation; a collation does not stop two numeric operands being compared as
numbers; the `i` flag was accepted and discarded.

### Root cause

A map entry is **a claim about meaning** — "SEL's `CHAR` means MariaDB's `CHAR`"
— and the only thing checking it is the `.sqlt` suite, which checks the *string*.
`func.text.char` asserting `CHAR(233 USING utf8mb4)` is green whether or not that
expression does what SEL's `CHAR` does. The suite is orthogonal to the claim.

The four caught during M1 were caught because I happened to paste the templates
into a `mariadb` shell. That is a habit, not a check. It did not survive into M2,
which is exactly when four more of the same kind shipped.

### Fix — a semantic oracle, with per-entry coverage

`sql/oracle/*.selo`: closed SEL expressions — literals only, no bindings — whose
expectation is not a string but an agreement:

```
### entry: funcs.CHAR
CHAR(233)
CHAR(65)
### entry: ops.IN
"OPEN" IN ("open", "held")
3.0 IN (3)
3 IN (1, 2, 3)
```

`tools/check-sql-oracle.sh` evaluates each with the SEL evaluator, translates it,
runs `SELECT <sql>` against a live server, and compares. Skips with a message
when no DSN is set, the way `impl_available` already skips a missing host.

**The part that matters is the coverage gate.** The generator already knows every
entry in every dialect. The oracle runner knows which entries each expression
touched. So it can report:

```
mariadb: 61 of 68 supported entries have an oracle expression
  no oracle: funcs.BACKWARDS funcs.CODE ops.BXOR ...
```

and fail below a threshold. That turns "did we check this entry means what it
says?" from a judgement call into a number, per entry, per dialect — and it makes
adding a dialect in M5 come with an explicit, visible obligation rather than a
hope.

**Would have caught:** all eight.

---

## 4. Class B — the corpus compared symmetric inputs

**Four defects**, overlapping Class A.

### Example

The M2 differential ran 73 expressions and passed. Every one of them looked like
this:

```
2.50 + 2.50        UPPER("abc")        FIND("a", "banana")
```

Operands the parser had already given the same form; arguments in the order the
template uses them. Nothing in the corpus compared `"OPEN"` against `"open"`,
`3.0` against `3`, or a function whose SQL spelling reorders its arguments. So
the case-collation bug, the numeric-scale bug and the binding-order bug all
passed 73 green comparisons.

### Root cause

The corpus was written by asking **"which functions have I covered?"** rather
than **"which distinctions could collapse?"** Case, scale, argument order,
quoting and NULL are all distinctions that are only visible when the two sides
of a comparison differ in exactly that way — and a corpus written function by
function never produces such a pair by accident.

### Fix — put SQL in the fuzzer that already exists

`tools/gen-programs.mjs` generates random programs and `tools/fuzz.sh` diffs them
across six hosts. The SQL layer is simply not a lane in it. It should be:
generate closed programs, evaluate, translate, execute, compare — with the
generator's existing bias toward awkward inputs (astral pairs, differing scales,
mixed case) doing the work of imagining asymmetric pairs, which is precisely
the thing a human corpus-writer is bad at.

**The lane has to execute both renderings, through native prepares.** This is
the part to get right, because getting it wrong reproduces the exact blind spot
being fixed. In `inline` mode every literal is quoted at the position it belongs
to, so an ordering error is *invisible* — the string is correct however the
parameter pool is arranged. Only `params` mode can show it, and only against a
real server: PDO's default `ATTR_EMULATE_PREPARES = true` inlines client-side, so
an emulated run passes whatever the binding order is. That is not hypothetical —
23 params-mode comparisons "passed" under emulation before the binding-order bug
was understood.

So the lane must render each program twice, execute the `params` form with
`ATTR_EMULATE_PREPARES = false` and the bindings the fragment reports, and
compare both against the evaluator. An inline-only lane would have caught the
collation and scale defects and would have been blind to the one that motivated
this section.

This is a small change to a tool that already exists and is already trusted, and
it reuses the batch-runner plumbing every host has. It is the highest
value-per-line item on this page.

**Would have caught:** the case-collation bug, the numeric-scale bug and — with
`params` mode in the loop — the binding-order bug. Probably several of Class A
as a side effect.

---

## 5. Class C — documented behaviour with no witness

**Five defects.**

### Example

`docs/SQL-TRANSLATION.md` §4.7 says registering an entry against `ansi` reaches
every dialect. `sql/MAP.md` §5 documents the `skel` escape hatch. `SPEC.md` §7.4
says `COUNT` is the number of children. §5.4 says a `value` list unrolls exactly
like a literal one.

Each of those sentences was true in the document and false in the code:
registering against `ansi` was shadowed by the pre-flattened tables; a registered
skeleton was stored under an upper-cased key nothing reads; `COUNT("hello")` was
`1`; `x IN <value binding>` was refused.

### Root cause

Cases were written by reading the **code** and asking what it does. A case
written that way cannot fail — it is a transcription. Nothing was written by
reading the **document** and asking what it promises.

The `+` array trap is the sharpest instance. `$base + ['scalarRule' => true]`
silently kept the left value, so the flag was permanently false. That is a PHP
footgun, but the reason it was *invisible for a whole milestone* is that
`scalarRule` had no case: its only observable effect was `COUNT` of a scalar,
and no case asked.

### Fix — make the design document executable

`tools/extract-docs.mjs` already pulls `EXPR  =>  RESULT` lines out of ```sel
blocks in `README.md`, `docs/LANGUAGE.md` and `docs/EXTENDING.md`, and
`check-docs.sh` runs them through every host. Documentation that cannot be
checked is documentation that drifts — the tool's own comment says so.

`docs/SQL-TRANSLATION.md` §7 is full of examples in exactly the right shape
already:

```
ALL((1, 2, 3), _ > 0)
    (((1 > 0) AND (2 > 0)) AND (3 > 0))
```

They are prose. Extending the extractor with a ```sel-sql block carrying
`SOURCE` / indented `SQL` pairs, plus a dialect and bindings header, would turn
every worked example in the contract into an executed case — and would have
caught the flat-versus-nested fold discrepancy that sat in §13.1 for two
milestones while §7.1 said something else.

For the claims that are not example-shaped — "registering against `ansi`
reaches every dialect" — the discipline is cheaper than a tool: **a normative
sentence gets a case name in the sentence.** `sql/cases/11-registration.sqlt`
now does this; nothing enforces it, and a grep for claims without a matching
case name would.

**Would have caught:** all five, and the doc drift as well.

---

## 6. Class D — an invariant asserted in a comment

**One defect, and it was the injection.**

### Example

`Fragment`'s docblock said the part list makes literal-confusion "unreachable".
It was not. A `value` binding declaring `type: NUM` is the one literal emitted
without quotes, and it reached the emitter straight from host data without ever
passing through `Value::num()`. `"1 OR 1=1 -- "` went out verbatim.

The claim was true of the path I had in mind and false of a path I had not.

### Root cause

**A part list stops a literal being *confused with* SQL. It cannot stop a
literal from *being* SQL.** The design conflated two different guarantees and
the prose asserted the stronger one.

### Fix — two, at different depths

*Now, done:* validate at the boundary and again in `Emit::literal`, which is the
last place before characters go out, plus cases for both.

*Deeper, proposed:* narrow the door rather than guarding it twice.
`Emit::literal` takes a `Value` and a form **string**, and any caller can ask for
the unquoted branch with anything. If the NUM form required `Dec::parse()`'s
output instead, the check would live in one constructor rather than at two call
sites.

This is worth stating precisely, because the obvious phrasing overclaims: PHP's
types would enforce that *a `Dec` was passed*, not that it was built honestly —
a caller can still construct one around an unvalidated string. It removes the
accidental path, not the determined one. That is still the right trade, and it is
a smaller claim than "unrepresentable".

The general rule this suggests: **every comment claiming something is
unreachable is a missing assertion.** They are cheap to find — grep for
"unreachable", "cannot", "never" in the SQL layer — and each one either becomes
a check or gets rewritten to claim only what is true.

**Would have caught:** the injection, at authoring time rather than at review.

---

## 7. Class E — a check that was itself unchecked

**Zero shipped defects, and the most interesting entry on the page.**

### Example

While fixing the reported slot bug I added a runner invariant: every parameter
slot must appear exactly once. I then reintroduced the bug deliberately to prove
the check fired.

**It did not fire** — because no case in the suite had a literal needle in an
`IN` list, so nothing exercised the path.

I strengthened the invariant to demand slots read `1, 2, 3` in output order. It
caught ten more instances immediately — and then turned out to be demanding the
wrong thing: `FIND(needle, hay)` maps to `INSTR({1}, {0})`, so the second slot
created is legitimately the first emitted, and the template is data. The real
fix was that `bindings()` had to walk the output rather than return the pool as
stored.

### Root cause

Two, and they compound. A new check is code, so it has the same defect rate as
any other code — and a check that never fails is indistinguishable from a check
that cannot fail. Separately, I asserted a property (creation order equals text
order) without first deriving it from a failure mode; had I written down *what
goes wrong if this is violated*, the reordering templates would have surfaced
immediately.

### Fix — mutation testing, as a committed tool

`tools/mutate-sql.sh`: a list of known-dangerous mutations to the SQL layer —
drop the `textCollate` from a comparison, splice a fragment twice, swap two
template arguments, return the params pool unwalked, use `+` where `array_merge`
is meant — each applied to a copy of the tree, with the suite expected to **fail**
for every one. A mutation the suite survives is a hole, reported by name.

The precedent is already in the repository: `tools/gen-sql-map.mjs`'s validation
was checked by planting seventeen malformed entries and confirming each was
caught. That worked, it took ten minutes, and it is the only check in the SQL
layer whose adequacy is known rather than assumed.

**Would have caught:** the fact that the first slot invariant was a no-op —
before it was committed and trusted.

---

## 8. Class F — a fix reached something that shared its structure

### Example

`"OPEN" $== "open"` was TRUE in MariaDB and FALSE in SEL, because the comparison
ran under a case-insensitive collation. The fix: apply `{textCollate}` to the
operands of the byte-comparison family. The family was selected by **variant
name** — every entry whose variant is `text`.

`&`, the concatenation operator, also has a variant named `text`. It received the
collation treatment too, and concatenation has nothing to do with byte
comparison.

That one was caught in the same pass, but only because a `&` case happened to
exist in `02-operators`. Nothing about the fix made it visible.

### Root cause

**The fix was applied along a structural axis, and the structure was wider than
the semantics.** "Variant named `text`" was a proxy for "compares bytes", and
proxies leak. The same shape is available anywhere a change is applied by key
prefix, by section, by naming convention, or by "everything in this table" —
which, in a data-driven layer, is most changes.

This is a distinct failure from A–E because the defect did not exist before the
fix. The suite could not have caught it earlier; there was nothing to catch. It
is introduced *by* the repair, which makes it exactly the kind of thing a review
of the diff misses, since the diff looks correct — it is doing what it says.

### Fix — name the members before you change the class

Two parts, both cheap:

1. **When a change keys on a structural property, enumerate every holder of that
   property in the commit message.** `grep -l '"variant": "text"' sql/dialects/`
   is three seconds and would have printed `&` next to `$==`. The discipline is
   not "grep more", it is: a fix that names a class must list the class.

2. **Plant it as a mutation** (item 4 below). "Apply the comparison-family
   transform to `&`" belongs in `tools/mutate-sql.sh` precisely because it is a
   plausible mistake made by a correct-looking change, and because a suite that
   survives it is a suite that would not have caught the original.

The deeper version, if the map ever grows: give the byte-comparison family its
own explicit key in the data rather than inferring it from a variant name shared
with unrelated entries. `isByteComparison()` in the Translator is currently that
explicit list, living in code — which works, and is worth remembering is a
*second* list that has to stay in step with the first.

**Would have caught:** the `&` regression, without depending on a case existing.

---

## 9. The evidence is not in the repository

This is not a class of defect. It is the class of *evidence* every section above
argues for, and it currently fails its own standard.

Three commit messages in this layer cite verification: "73 expressions agree with
MariaDB", "23 agree under native prepares", "7 rules select identical rows". All
three are true. None of them can be re-run by anyone, including me tomorrow — the
harnesses that produced them (`diff-mariadb.php`, `params-diff.php`,
`m3-rows.php`) live in a scratch directory outside the tree.

That is the same defect as Class C in a different costume: a claim in a document
with no witness attached. A number in a commit message that nobody can reproduce
is a belief with a decimal point on it.

**Fix:** commit those three files as the seed of `sql/oracle/`. They are already
written, already known to work, and already do most of what item 2 describes —
they compare a translation against a live server instead of against the author.
What they lack is a runner, a skip-when-absent guard for machines with no
MariaDB, and a coverage gate. That is a much smaller job than writing an oracle
from nothing, and it is the honest first step of items 1 and 2 rather than a
separate task.

---

## 10. What to build, ranked

| # | Item | Effort | Class | Defects it would have caught |
|---|---|---|---|---|
| 0 | Commit the three scratch differentials as `sql/oracle/` | trivial — already written | §9 | makes 1 and 2 possible |
| 1 | SQL lane in `tools/fuzz.sh`, **both render modes, native prepares** | small — the plumbing exists | B, A | ~6 |
| 2 | Oracle runner + per-entry coverage gate over item 0 | medium | A | 8 |
| 3 | `sel-sql` blocks in `extract-docs.mjs` | small | C | 5 + doc drift |
| 4 | `tools/mutate-sql.sh`, incl. the `&` planted mutation | medium | E, F | validates 1–3 |
| 5 | Typed literal at the emitter | small | D | the injection |
| 6 | Lint banning `+ [` in `php/src/Sql/` | trivial | C | 2 |
| 7 | Enumerate the class in the message when a fix keys on structure | free | F | the `&` regression |

Item 0 is first because it costs nothing and items 1 and 2 are both extensions of
it. Items 1 and 3 are small because the tools already exist and the SQL layer
simply is not wired into them. That is the whole shape of this document: **the project
already knows how to test things properly, and the SQL layer opted out.**

Item 2 is the one that changes what "done" means for M5. Three more dialect
documents are about to be authored, each a few dozen semantic claims about a
server none of us has probed. Without a coverage gate they will be authored the
way `mariadb.json` was — plausibly — and the four defects M1 found by hand are a
lower bound on what that produces.

---

## 11. What not to do

**Do not write more `.sqlt` cases as the answer.** They are the right tool for
pinning a decision and for regression, and 180 of them do that well. They cannot
find a defect nobody suspected, because a case is a belief and the defects are in
the beliefs.

**Do not treat the review as the fix.** Three rounds of review found real
defects, which means review works — and it also means review is currently
load-bearing. The point of items 1–6 is to make the next review work harder for
its findings, not to replace it.

**Do not add a static analyser expecting it to catch these.** PHPStan would flag
none of the twelve. They are semantic — a wrong claim about a server, a wrong
claim about a document, a wrong claim about an invariant. The oracle, the fuzzer
and the executable documents each replace a claim with a measurement, and that is
the only thing that has actually worked here.
