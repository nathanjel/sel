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

**The part that matters is the coverage gate**, and it is built. Two things about
how it came out differ from the sketch above, both for the better.

*No threshold.* The sketch said "fail below a threshold". A threshold is exactly
the "broad set with hidden assumptions" this project rules out: 94% coverage is a
number that sounds like progress and names nothing. The rule is per entry —
either an expression reaches it, or `sql/oracle/coverage.json` carries a written
reason. The exclusion list is empty and that is the state to keep it in.

*Measured, not declared.* Coverage comes from a trace of `Map::entry()`, the one
lookup every op, func and skeleton passes through, and the `### entry:` headers
are checked **against** the trace rather than trusted as it. This matters more
than it sounds: four headers in the first corpus were wrong. `ALL((1,2,3), _ > 0)`
was filed under `skel.all` and reaches no skeleton at all — a static list unrolls
into the operators' own templates, which is the property that makes
`ALL((a,b), p)` and `p(a) AND p(b)` the same bytes. A declared coverage table
would have recorded four entries as covered that nothing exercised.

Testing the gate found two holes in the gate, which is item 7 arriving early:

- A **mislabelled** group passed, because groups were keyed by header name and a
  mislabelled one hid behind an honest group with the same label. A group is an
  occurrence now, not a name.
- A **typo'd** header — `func.UPPER` for `funcs.UPPER` — was silently ignored,
  because the checker did not recognise it as a claim. That is the quietest way
  for a coverage gate to lie: it reports success about a question it never asked.
  A header is now either `<section>.<KEY>` or an explicit `mixed:<label>`, and
  anything else is a suite error.

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

### What it found

Built as `tools/fuzz-sql.sh`. On its first 4000-program run, against the code
that had just passed the oracle, the review and the case suite, it found six:

| Defect | Shape |
|---|---|
| `params` mode changed a number's text | `CAST(? AS DECIMAL(65,10))` pads: `TRIM(2.50)` gave `"2.5000000000"` |
| the BIN family read numbers as numbers | `HEX(1)` is `"1"`; SEL's `TO_HEX(1)` is `"31"`, the hex of the character |
| mixed known kinds unified to UNKNOWN | `IF(TRUE, TRUE, "A-1")` is `"TRUE"` in SEL and `1` on the server |
| a BOOL byte-compared to a number | `CAST(0 AS CHAR)` and `CAST(FALSE AS CHAR)` are both `'0'`; SEL says the kinds differ |
| `MIN`/`MAX` rescaled their result | `LEAST(17, 123.456)` is `17.000`; SEL's `MIN` returns `17` |
| a `SelError` escaped `Sql::translate` | an unportable regex in an untaken branch — fatal where `tryTranslate()` promises `null` |

The first is the one to sit with: **it was introduced by the previous fix.** The
oracle found that `params` mode compared numbers as text; the fix cast NUM slots
back to numbers; and the cast changed what those numbers looked like as text.
That is Class F — a repair reaching along a structural axis wider than the
semantics — and it was caught within the hour by the next check built. Neither
the case suite nor the oracle could have: the oracle's corpus has no expression
that reads a computed number as text, because nobody thinks to write one.

The eventual fix was smaller than either attempt. No coercion of a bound string
reproduces a bare decimal literal — `CAST` pads the scale, `+ 0` drops it and
floats above seventeen digits — because MariaDB reads `2.50` as DECIMAL with
scale 2 and a parameter is untyped. So a NUM literal is rendered as itself in
every mode and binds nothing, which costs exactly what item 5 established there
was nothing to protect: after `Emit::numericLiteral` a NUM literal is digits, one
`.` and a leading `-`, by construction.

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

### Fix — make the document quote what runs

*Built as `php/bin/sqldoc` and `tools/check-sql-docs.sh`.* Not the second
extractor the sketch below proposed — see the note at the end of this section.

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

They are prose. The sketch was to extend the extractor with a ```sel-sql block
carrying `SOURCE` / indented `SQL` pairs plus a dialect and bindings header.

**That is not what was built, and the reason is worth keeping.** `extract-docs.mjs`
is a *language* checker: it writes four index-aligned files and `check-docs.sh`
feeds them to `impl_batch`, which all six hosts implement. A SQL example fits
none of that — it needs a dialect, half of §7's examples need a bindings
side-channel the `EXPR => RESULT` line has no room for, the subquery
expectations are multi-line where `.want` is one line per example, and the runner
would have to be `sqlt` rather than `impl_batch`. It would share a sixty-line
scanner and nothing else, while adding a second documentation-example format to a
project that has one.

`sql/cases/*.sqlt` already *is* the format that sketch would have had to invent:
`dialect`, `bindings`, multi-line `expect`, and a runner. So the block names a
case and quotes it, and `php/bin/sqldoc` asserts the quotation is exact:

```
​```sel-case agg.static.all
ALL((1, 2, 3), _ > 0)
    (((1 > 0) AND (2 > 0)) AND (3 > 0))
​```
```

The layout is the one the document already used, so converting a block is a
change of fence and nothing else. Twelve of §7's examples are quotations now, and
converting them found two more drifts immediately: §7.2's `columns` example
claimed three columns where the case has two, and its binder was named `VALUES`
in the document and `V` in the code.

Being precise about what this buys: it checks the document against the suite, and
the suite is an assertion suite. It stops the document lying about the code; it
does not make the code right. That is `sql/oracle/`'s job, and where a documented
example is a closed expression it inherits the oracle for free, because both run
over the same case files.

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

*Deeper, built:* narrow the door rather than guarding it twice.
`Emit::literal` takes a `Value` and a form **string**, and any caller can ask for
the unquoted branch with anything. If the NUM form required `Dec::parse()`'s
output instead, the check would live in one constructor rather than at two call
sites.

What was actually built is smaller and better than the proposal. `Emit::literal`
now routes its NUM branch through one private `numericLiteral`, which parses the
text and emits **what the parse recovered** — `Dec::format`'s output — rather
than the string the caller supplied. The two agree for everything the parser
produces, and the difference is the point: proving a string is a number and then
emitting a *different* string is a gap, however small, and the gap is where
`"1 OR 1=1 -- "` lived. After it, the characters that can leave unquoted are
digits, one `.` and a leading `-`, by construction rather than by guard.

It is worth stating what that does not do, because the obvious phrasing
overclaims. `Dec::parse` is still being called on host data, and a host with its
own reason to believe a string is a number can still be wrong about it. What it
cannot be is wrong in a way that reaches the server. The accidental path is
closed; a determined one is not, and no type in PHP would close it either.

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

*Built as `tools/mutate-sql.sh`, with the mutations as data in
`sql/mutations.json`.* Eighteen of them, each a defect this layer has actually
shipped or a plausible neighbour: drop the `textCollate`, swap `INSTR`'s
arguments, return the params pool unwalked, use `+` where `array_merge` is meant,
add `&` to the byte-comparison list, upper-case every registration key, drop the
`(?s)` prefix, choose the regex flag by argument count. Each is applied to a copy
of the tree and the checks run in order — `sqlt`, `sqldoc`, then the three oracle
modes — until one fails. The first that does is reported, because it answers
"what would have told you" with the cheapest true answer.

Three properties make it a check rather than a gesture:

- **A stale pattern is a suite error, not a pass.** If `from` does not occur
  exactly once the mutation is reported as stale, because a pattern that matches
  nothing would otherwise be scored by whatever the checks say about *unmutated*
  code — this tool's own failure mode, and the reason it exists.
- **The mutation is proved to have landed**, by re-reading the file.
- **No database means skipped, not caught.** A mutation that survives `sqlt` and
  `sqldoc` on a machine with no DSN is reported as skipped, because the check
  that would have caught it did not run.

**It found two holes on its first complete run**, both in refusals added an hour
earlier from fuzz findings: removing the mixed-kind refusal and removing the
BOOL-comparison guard each survived every check. Nothing pinned them — the fuzz
lane had found the defects, the fixes went in, and no case was written. Then
fixing the first hole exposed a second: the case pinning the BOOL guard used
`0 IN FALSE`, which is lowered by `inOperator`, so deleting the same guard from
`binary()` still survived. Two paths, one rule, one case.

That is the pattern the whole document is about, arriving one more time: **a fix
without a case is a fix that lasts until someone tidies the code.** The mutation
runner is what turns "we fixed that" into something checkable.

The precedent was already in the repository: `tools/gen-sql-map.mjs`'s validation
was checked by planting seventeen malformed entries. That worked, it took ten
minutes, and it was the only check in the SQL layer whose adequacy was known
rather than assumed.

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
| ~~6~~ | ~~Lint banning `+ [` in `php/src/Sql/`~~ | **dropped** — see below | | |
| 7 | Enumerate the class in the message when a fix keys on structure | free | F | the `&` regression |

**Item 6 was dropped rather than built, and the reason is worth keeping.** The
proposal was to forbid PHP's array-union operator in `php/src/Sql/`, since using
it where `array_merge` was meant caused two defects. Six uses remained in the
tree and **all six were correct**: three are `$b + ['type' => 'UNKNOWN']`, which
is `+` doing exactly its job — keep what the host declared, supply a default —
and where `array_merge` would be the bug; three merged disjoint slot maps. Six
false positives, zero true positives, and no possible improvement, because the
correct default-idiom and the buggy override-idiom are *the same syntax*. The
defect was never `+`; it was intending "override" and writing "default". A lint
that fires on correct code gets a suppression comment, and a codebase that trains
you to add the suppression comment is worse than one with no lint.

What replaced it says the same thing where it can be true. The three disjoint
merges relied on `relationSlots()` never naming a slot its caller also names —
true by inspection, enforced by nothing. `Translator::slots()` now merges them
and raises `LogicException` on a collision. Mutating `relationSlots` to return a
`body` key turns four silently-wrong translations into four loud failures.

That is the general shape: **a lint bans a spelling; an assertion states the
property the spelling was standing in for.** Prefer the second wherever the
property can be named.

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
