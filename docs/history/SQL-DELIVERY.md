# SEL → SQL: how it was delivered

Sections 14–17 of `docs/SQL-TRANSLATION.md`, moved here when the layer shipped
in 0.4.0. They are the plan and the reasoning, not a description of what the
code does now — the delivery sequence has been delivered, the alpha has been
marked, and the decisions are taken. Kept in full because they say *why* the
layer is shaped the way it is, which the reference document deliberately does
not repeat.

For what the layer does today, see [../SQL-TRANSLATION.md](../SQL-TRANSLATION.md).

---

## 14. Delivery sequence

Milestones, each one leaving the tree green.

**M1 — the map exists. DONE.** `sql/MAP.md`, `sql/errors.md`, `sql/dialects/ansi.json`,
`mysql-family.json` and `mariadb.json`, `tools/gen-sql-map.mjs`, `tools/check-sql-map.sh`, and the
generated PHP file. No translator yet; the deliverable is that the data is
authored, validated and generated.

**M2 — PHP translates the core. DONE.** Stages 1, 3 and 4 for operators, literals,
`column` bindings, `IF`/`COND`, and the non-aggregate functions. `sql/cases/`
covering `lex.*`, `op.*`, `func.*`, `norm.*`, `refuse.*`; `php/bin/sqlt`;
`impl_sql` wired into `check.sh`.

**M3 — PHP does aggregates. DONE.** Stage 2, all three shapes, the FILTER rewrites,
the `agg.*` cases.

**M4 — PHP + MariaDB end to end. DONE**, and delivered somewhere other than
planned. The gate was "a schema, a rule pushed into a `WHERE`, the same rule
evaluated in PHP over the same rows, and an assertion that the two select the
same ids". That is `sql/oracle/rows.json` and `sql/oracle/fixture-*.sql`, run by
`php/bin/sqlo rows` — a committed, re-runnable check in `tools/check.sh` rather
than an `examples/sql-php.php` nobody would run twice. The first version of it
was an example, and it is the reason `docs/history/SQL-TESTING.md` §10 exists: it lived in
a scratch directory and its results were cited in three commit messages nobody
could reproduce.

**M4½ — the checks the layer was missing. DONE.** Six items from
`docs/history/SQL-TESTING.md`: the semantic oracle, its coverage gate, the SQL fuzz lane,
executable documentation, mutation testing, and the emitter's narrowed literal
path. Not in the original plan, and it found seventeen defects in code that had
already passed three reviews.

**M5½ — the corpus pushed to the edges. DONE.** Zero, empty, very long,
negative, fractional-where-a-count-belongs, and numbers past what a server's
decimal type holds. It found three defects, one of them the largest single one
in `docs/history/SQL-TESTING.md`: for four milestones the translator had never asked
whether the expression it was translating was a **valid SEL expression**.
`LEFT("abc", -1)` translated into all four dialects and they answered `''`,
`''`, `'ab'` and `'abc'`; SEL raises `E_RANGE`. Seventeen expressions behaved
that way. §11.4 is the fix — where every leaf is a literal, ask SEL's own
evaluator — and §11.3 records the two number-range boundaries the same
extension turned up. The corpus gained a line form, `!E_RANGE LEFT("abc", -1)`,
that asserts a translation does **not** happen.

**M5 — MySQL, PostgreSQL and SQLite maps. DONE.**

MySQL was authored the other way round from every dialect before it: the leaf was
written **empty** — `extends: mysql-family` and nothing else — and the oracle was
asked whether the claim held rather than a person being asked to remember. It
did, on MySQL 8.4.11: the corpus agrees entry for entry, the same expressions are
refused, all 10 row rules agree, and 6,000 generated programs give numbers
identical to MariaDB's — identical, M5½ later established, below 30 fractional
digits, which is where MySQL's DECIMAL stops and MariaDB's does not (§11.3). One entry moved *up*: `RMATCH` had been sitting in `mariadb.json`
because at M1 there was no MySQL to check it against, and the family layer means
"verified to agree", not "probably agrees".

That the leaf stays empty is now a check rather than a memory. `php/bin/sqlt`
re-runs every `mariadb` case under `mysql` and requires the same string, the same
error and the same parameters — 256 of them today — so an override added to one
leaf and not the other fails the suite. A second check compares the two leaves
*entry by entry* through `Map::entries`, which catches an override that changes
no output character, such as a `caveat`; `MIRROR_EXCEPTIONS` names the one entry
allowed to differ, and removing that name is itself a mutation. The alternative was a `14-mysql.sqlt` of
copies, two hundred lines asserting that a copy is a copy.

The plan said "data and cases only; the translator does not change. If it does,
that is a bug in the M1 design and should be fixed as one." The translator
changed three times for SQLite, and the plan's own test is the right way to score
them:

- **`arity` was never enforced.** `sql/MAP.md` §4.1 has documented it since M1 as
  "how PostgreSQL refuses the three-argument form its `POSITION` cannot express
  — graceful degradation as data, with no host code involved". There was no host
  code involved and no degradation either: the generator validated the field and
  nothing read it. A bug, exactly as the plan predicted, found the hour SQLite
  was written. It was about to do the same nothing for PostgreSQL.
- **BOOL literals were parameterised.** `1 = '1'` is `0` in SQLite, so
  `TRUE XOR TRUE` answered TRUE in `params` mode and FALSE inline. A bug in M2's
  render model, not new design.
- **`numericLiteral`** is the one genuine addition to the map's surface: one
  lexical key so a dialect can say how it spells a number. SQLite needs it
  because it has no decimal type at all, which is not something ANSI, MySQL or
  PostgreSQL will ask for.

So: two M1/M2 defects the second dialect exposed, and one new key. The prediction
held.

**M5¾ — five reviewers, briefed on the contract. DONE.** Not a milestone in the
plan, and the largest single round of defect-finding the layer has had. Five
independent lanes — the map, the emitter, the bindings, the translator, the check
suite — were each given the engineering promise (*if a translation passes, the
contract holds; otherwise fail outright*) and asked what violated it. They
reproduced **twenty-four** contract violations against a suite reporting zero
differences across four live servers — 273 cases, 10 row rules and 39 mutations,
all green — plus twelve faults in the checks themselves, eight of them mutation
classes that survived every check there was. Twelve commits, most carrying cases,
corpus lines and a mutation of their own. The suite now stands at 339 cases, 391 corpus
expressions across four dialects at 0 differ, 13 row rules and 69 mutations
caught with none surviving.

The finding under the findings is the one worth carrying forward: **every severe
defect needed a binding**, and the corpus is closed by design. The suite was
measuring the map, and the map was right. §11.5 is that lesson written down, and
`sql/oracle/rows.json` is where the answer to it lives.

**M6 — Python port. DONE.** Transcribed from the PHP, generated map consumed
as-is, the same `sql/cases/` suite passing byte-identically:

| | passed | mirrored |
|---|---|---|
| `php/bin/sqlt` | 339 | 256, plus 1 leaf pair compared entry by entry |
| `python/bin/sqlt` | 339 | 256 |

That equality is the whole point of §13.1, and it held on the first full run:
326 of 339 passed immediately, and the other 13 were two wrong *helper names* —
`Regex::portableSource` is `regex.validate` here and `Utf8::codePoints` is
`utf8.to_code_points` — not two wrong translations. Which is the result the
milestone was designed to produce: the map is data, so a second host consuming it
has almost nothing left to get wrong, and what it does have is spelled in its own
language rather than in the design.

Three things needed a decision rather than a rename, and each is a case where the
obvious Python spelling is a *different function* from the PHP one:

- **`isset` is not `in`.** PHP's `isset($a['k'])` is false for a key holding an
  explicit null; Python's `in` is true. `{"kind": "value", "value": null}` was
  `E_SQL_BINDING` in one host and `E_SQL_SHAPE` in the other — the same refusal
  under a different code, and codes are contract. `in` is right only where PHP
  wrote `array_key_exists`, in `Map::entry` and `Map::entries`, where a null
  entry is a *withdrawal* and finding it is the point.
- **`str.upper()` is not `strtoupper`.** It folds `ß` to `SS` and changes the
  name's length; `strtoupper` is ASCII-only. This host already knew — its
  registry uses an `ascii_upper` and says why — and all eleven case-folds in the
  port use it.
- **PHP's `+` on arrays is left-wins and Python's `{**a, **b}` is right-wins.**
  Every site had to be read for which of *default* and *replace* it meant. Both
  are noted at the site; getting the first backwards silently makes every
  declared type `UNKNOWN` and the kind guards stop guarding.

The check suite grades the built **wheel** as well as the source tree:
`python/bin/sqlt` adds `python/` to `sys.path` only when `sel` is not already
importable, so `SEL_IMPLS=python-wheel` measures what would be installed rather
than shadowing it with the sources.

Two of `php/bin/sqlt`'s five checks are deliberately not ported, and the runner's
docstring says so where somebody would go to add them: `caveat_pins` and
`mirror_parity` ask about `sql/cases` and `sql/dialects`, which are shared data
that PHP already measures. Running them twice measures the same thing twice.
That is also why `python/sel/sql/map.py` has no trace facility.

**M6½ — the leakage round. DONE.** An adversarial review asked one question:
why does a parsed AST, driven by a map generated from one source, become
different SQL depending on which language the translator was written in? The
answer was that the host's own rules were deciding — PHP's `(bool)"0"`,
`empty("0")`, `intval`, `isset`-versus-`in`, `str_replace('')`, left-wins `+`;
Python's truthiness, strict `int()`, right-wins merge, recursion limit — and none
of it was a decision anybody made.

Four fixes, in the order they were made, and the first is the one worth
remembering because no PHP-against-Python comparison could ever have found it:

- **Keys are strings.** `V["01"]` and `V["1\n"]` resolved to element 1 in *both*
  hosts, where SEL says `E_NO_KEY` — `^[0-9]+$` accepts a trailing newline in
  both languages and both integer parsers swallow leading zeros. The translator
  no longer hands a key to its host's integer parser: a key names a position iff
  it fully matches `[1-9][0-9]{0,8}`.
- **`E_SQL_DEPTH`.** Both hosts translated expressions the evaluator refuses with
  `E_DEPTH`, so the database answered a rule SEL has no answer for. The walk is
  bounded at `Evaluator::MAX_DEPTH`, **read** from there rather than copied.
- **Bindings are constructors.** The API took a nested map shaped like JSON and
  validated that shape by hand in each host; §5 has the full account.
- **Registration is validated against emitted data.** Everything the generator
  requires of a shipped entry, `Map::define` now requires of a registered one,
  against a `RULES` block the generator emits rather than each host retyping the
  list. Two of the divergences it closes were wrong in *both* hosts at once: a
  `textEscape` given as a string made both skip escaping and emit `'it's'`
  unquoted, and a `null` lexical — a documented withdrawal — was read as absent
  and the base's value inherited in its place.

`sql/cases/18-host-neutrality.sqlt` pins every one, because a case file is the
only witness the next four ports inherit automatically: mutations target PHP and
JSON, and `sql/oracle/` is PHP-only.

**M7 — superseded, and the reason is worth keeping.** *(The replacement it
named — translate each fuzzed program with every host that has a translator
and diff the result, no database needed — was finally built: `impl_sqlfuzz`
and the first half of `tools/fuzz-sql.sh`. Until then the whole SQL fuzz lane
was a no-op wherever no DSN was set.)* As planned this was
"`python/tests/sql/`, the differential runner, the three backends" — a second
database harness in the second host. It is not being built. `sql/oracle/`
measures whether the *map* means what SEL means, and the map is data both hosts
consume unchanged; a second copy of that harness would ask a live server the same
question twice and answer about `sql/dialects/*.json` both times. The oracle
stays PHP-only.

What deserves the slot instead is the differential the ports actually make
possible: `tools/fuzz-sql.sh` already generates thousands of programs and
translates each one with PHP, and translating each with Python and comparing the
*string* — or the refusal code — is the six-host differential applied to a
seventh, needs no database at all, and is the check that turns *passes the suite*
into *is the same translator*. §11.5 is the argument for it: 339 assertion cases
are 339 beliefs, and both of the transcription defects above were found by
reading rather than by running.

**M8 — alpha. NOT DONE, and it is the only one.** Commit to GitHub and mark it
with a lightweight tag. Nothing is published to Packagist, npm or PyPI, and
`tools/check.sh` is not required to be green: the point of the tag is to be able
to name the state the PHP+MariaDB proof passed in, not to ship it. See §15.

The tag was never cut. Work carried straight past it — M9, then the parser
migration, then three rounds of limits — so the state it was meant to name is
long gone, and the honest options now are to tag the present or to drop the
milestone. It is left open rather than quietly deleted because an unmarked
milestone in a tracker is how a tracker stops being one.

**M9 — JS port. DONE.** Transcribed from the Python, generated map and generated
case table consumed as-is, the same `sql/cases/` suite passing byte-identically:

| | passed | mirrored |
|---|---|---|
| `php/bin/sqlt` | 368 | 266, plus 1 leaf pair compared entry by entry |
| `python/bin/sqlt` | 368 | 266 |
| `js/bin/sqlt.mjs` | 368 | 266 |

(The counts are M9's, and are left as they were: the suite has grown to 377 since,
and a milestone record that silently tracks the present is not a record.)

All 368 on the first full run, which is what M6 predicted would happen the second
time: the map is data, so a third host consuming it has almost nothing left to
get wrong. `tools/gen-sql-map.mjs` and `tools/gen-sql-cases.mjs` each grew one
emitter and one line in `OUTPUTS`; the PHP and Python outputs regenerate
byte-identical, which is the check that this added a host rather than changing
the map.

Four things were host-shaped rather than transcribed, and each is a place where
the obvious JS spelling is a *different* thing from the Python one:

- **`Map`, not `{}`, wherever a key comes from a rule or an application.** The
  runtime overlay, the bindings table, `normalise`'s definitions and the
  translator's binder frames all key on caller-supplied text, and `{}` answers
  for every `Object.prototype` name. Reads of the generated tables go through
  `Object.hasOwn` for the same reason.
- **`Map`, not an object, for an aggregate's elements.** A plain object with the
  keys `"1"`, `"2"`, … iterates in ascending *numeric* order rather than
  insertion order, so a `clist` mixing `"a"` with `"1"` would have unrolled in a
  different order here than in the other two hosts — silently, and only for
  indexed assignment.
- **`Object.create(null)` for a relation's fields.** A field named `__proto__`
  assigned into an object literal sets the prototype instead of a property.
- **The emit/fragment cycle needs no help.** Python breaks it with a
  function-local import; ESM resolves it natively, because neither module touches
  the other's binding while they are still evaluating.

`asciiUpper` moved into `js/src/lexer.mjs`, where the other hosts keep it, and
`MAX_DEPTH` is now exported from `js/src/eval.mjs` so the translator's guard can
read the evaluator's own limit rather than repeating 200.

The bundle does not carry it: `dist/sel.mjs` is built from `js/src/sel.mjs`, and
the SQL layer is a separate entry point (`package.json` `"./sql"`), so a host
that wants only the evaluator does not pay for the translator. `impl_sql
js-bundle` returns 0 for that reason rather than skipping something.

**C++ has landed.** It was a transcription of a design three hosts had already
agreed on, which was the cheapest moment to do it. It grades on the same three
lanes they do: 383 `.sqlt` cases plus their mirrors, the 2000×4 translator fuzz
diffed against js/php/python line for line, and the map replay — 217
registration calls, 564 lookups, 0 differences.

Two things it does differently, both because C++ has types where the others
have shapes. Its map is `constexpr` static arrays rather than a language
literal, so the replay check compares two genuinely different implementations
instead of one against a near-copy of itself. And twelve `.sqlt` cases whose
whole point is a malformed binding — a JSON array where a name belongs, a
section that is not one of the three — cannot be *written* with its typed
constructors: the compiler refuses them one stage earlier than the other hosts
do. Those are reported as refused by the type system rather than skipped, so
the total stays honest.

Porting it also closed two gaps in the shared corpus that had nothing to do
with C++: nothing asserted that a BIN slot is inlined rather than bound, and
nothing asserted that escaping applies the longest rule first. Both were found
by mutating the C++ layer and watching the mutation walk through all 381 cases.

**Lisp has landed too, and it was the last one.** All five hosts now translate,
and all five are graded by the same three lanes: 384 `.sqlt` cases with their
mirrors, the 2000×4 translator fuzz diffed across every host, and the map replay
at 217 registration calls and 564 lookups.

It needed no wrapper node type, where C++ did: a SEL node's child slots are
untyped in Lisp, so the `clist` indexed assignment builds sits in one directly,
exactly as Python's does. What it did need was a `lower-anchors` flag on
`validate-pattern`. This host lowers `^` and `$` to `\A` and `\z` for cl-ppcre,
whose `$` also matches before a trailing newline — and the Python host keeps
that lowering in a separate pass for the stated reason that validate()'s output
is shared with hosts whose engines have no `\A`. A translated pattern goes to a
server, which is one of those.

A note on how to build M2–M3 and M5–M6: those are the phases where fanning work
out pays. Authoring four dialect documents, writing the case files per category,
and transcribing PHP→Python are largely independent and verifiable against a
fixed suite. I have not launched anything — say the word and I will run it as a
workflow, which will spawn on the order of a dozen agents and cost accordingly.
M1 and M4 should be done in one head; they are where the decisions are.

---

## 15. Marking the alpha

**The alpha is a commit and a lightweight tag. Nothing is published.**

That decision removes a constraint rather than imposing one.
`tools/check-version.sh` compares seven manifests by **literal string
equality**, has no notion of a prerelease, and the ecosystems spell one three
incompatible ways — npm `0.4.0-alpha.1`, PyPI `0.4.0a1`, Conan and CMake
something else again. Putting any of those in the manifests fails the check
today. Since nothing is being published, none of it applies: the manifests keep
whatever plain version they carry, the tag names the commit, and the spelling
problem is deferred to the first *published* prerelease, where the fix is a
ten-line normalisation table in `check-version.sh`.

`tools/check.sh` is likewise not a gate on the tag. It runs eight configurations
of a language of which three have a SQL layer; a red step somewhere in that
matrix is not a reason to withhold a marker on work that has been proven
end to end against a real MariaDB. **M4 is the gate that matters.** The tag is
bookkeeping.

---

## 16. Out of scope

Stated as out of scope in the brief, and confirmed here:

- translating SEL into stored procedures or user-defined functions;
- building whole statements — `SELECT`, `FROM`, `JOIN` and `GROUP BY` are the
  application's, and the translator emits an expression to put in them;
- guaranteeing that a translated rule succeeds. Type errors at the database are
  an accepted outcome at this stage; that is the "best effort" the brief asks
  for, and §11 is the honest list of where it bites.

Deliberately deferred, and named here so it is a decision rather than an
oversight:

- **Named and driver-specific placeholder styles.** `params` mode (§9) ships in
  M2 with `?` and `$n`; `:name` and anything a particular driver prefers can be
  added as one more lexical entry when something needs it.
- **A precedence-aware emitter**, to stop wrapping everything in parentheses.
- **Date and time.** SEL has no date type, so there is nothing to map yet.
- **`ORDER BY` / window constructs.** SEL has no ordering vocabulary.

---

## 17. Decisions taken

Recorded so the reasoning survives the conversation that produced it.

**Unrolling is the default; a subquery is what you ask for.** When a host can
express a list either way, bind it as `columns` and let stage 2 unroll it. §5.5
shows both renderings of the same rule side by side. A `relation` binding is for
the case where the element count is genuinely not known until the query runs.

**One error class, and the message is the explanation.** `SqlError` with a code,
a descriptive message and a position. No diagnostics object, no `explain()`, no
second channel. `translate()` throws it, `tryTranslate()` returns null, and an
application that wants the reason calls the first (§10).

**`ansi` and `mysql-family` are bases, never targets.** Naming either in a
`translate()` call is `E_SQL_DIALECT`. A dialect no server implements is not a
dialect anyone should be able to aim at (§4.2).

**MariaDB is the reference.** The end-to-end proof (M4) runs against MariaDB —
11.8 locally — and `mariadb.json` is authored first. `mysql.json` is its sibling
under `mysql-family` rather than its parent, so the tested dialect never
inherits from an untested one (§4.2).

**Literals live in a part list from the first line of the renderer.** Inline
output stays the default and stays byte-exact for the suite, but the renderer
never concatenates a literal into a string, so `params` mode is a way of joining
rather than a second code path. `~n~` is the debug spelling of a slot, chosen
because SEL has no `~` token (§9).

**The alpha is a commit and a lightweight tag, published nowhere.** M4 is the
gate; the tag is bookkeeping (§15).
