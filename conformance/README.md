# SEL conformance suite

These files are normative. An implementation is correct when it passes all of
them; when two implementations disagree, this suite and `spec/` decide which is
wrong, not either implementation.

The format is plain line-oriented text rather than JSON, so that a future port —
Python, Lisp, anything — needs no parser other than the one it is already
writing. Setup is written **in SEL**, which removes the need for a second data
format entirely.

---

## File format

```
### name: text.left.basic
--- setup
name = "Za\u{17C}\u{F3}\u{142}\u{107}"
--- source
LEFT(name, 3)
--- expect
text "Zaż"
===
```

A line is a **marker** if it starts with `### `, starts with `--- `, or is exactly
`===`. Everything else is content, taken verbatim.

**A section's content is its lines joined with `\n`, with leading and trailing
runs of space, tab, CR and LF removed — those four characters and no others.**
That sentence is normative for the five readers, and it is fussier than it
looks. Each host has a trim function to hand and no two of them strip the same
set: JavaScript's `String.prototype.trim()` removes ECMA-262's WhiteSpace, which
includes U+FEFF and every Unicode `Zs`; Python's `str.strip()` removes Unicode
whitespace but not U+FEFF; PHP's `trim()` adds NUL and a vertical tab; C++ and
Common Lisp were already spelling out the four.

Reaching for the native one is therefore a way to run a *different program* in
one host than in another, invisibly. `lex.space.bom-is-not-whitespace` is the
case that found it: four hosts raised `E_SYNTAX` at the byte-order mark and
JavaScript answered `TRUE`, because its reader had deleted the BOM before the
lexer ever saw it. The lexers had agreed all along. The same trap is why
`tools/README.md` is normative about the corpus format's trailing newline.

The four are SEL's own whitespace, which is the only set that can be right here:
a reader that strips something the language does not is asserting that two
different programs are the same one.

| Marker | Meaning |
|---|---|
| `### name: <id>` | starts a case; `<id>` must be unique across the whole suite |
| `--- note` | prose, ignored by the runner |
| `--- setup` | SEL source evaluated first against an empty context; the resulting context is then given to `--- source`. Optional. |
| `--- source` | the SEL source under test. Required. |
| `--- expect` | one expectation line. Required. |
| `===` | ends the case |

Blank lines and lines beginning with `%` **outside** any case are ignored, so
files can carry headers and section breaks. Inside a section every line is
content, including ones beginning with `#` — those are SEL comments, not file
comments.

An error raised by `--- setup` is a **suite bug**, not a test failure, and the
runner reports it separately.

---

## Expectation forms

One per case.

| Form | Asserts |
|---|---|
| `text "…"` | kind TEXT with exactly these code points |
| `num 0.3333333333` | kind TEXT with exactly this content — sugar for `text`, unquoted, for readability |
| `bin 00ff10` | kind BIN with exactly these bytes, lower-case hex |
| `bool TRUE` / `bool FALSE` | kind BOOL |
| `none` | kind NONE with no children |
| `tree …` | full structure — see below |
| `error E_CODE` | that code, position not checked |
| `error E_CODE at 3:11` | that code at that 1-based line and column |

Quoted text in `text "…"` uses a **fixed, tiny escape set handled by the runner
itself** — `\\`, `\"`, `\n`, `\t`, `\r`, `\uXXXX` — deliberately *not* SEL's
lexer. The suite must not validate the lexer with the lexer.

## The `tree` dump

`tree` compares against a canonical dump that every implementation must emit
**byte for byte**. Agreement on the dump is itself part of what is being tested.

```
dump(v)  = scalar(v) + children(v)

scalar:    NONE -> "-"
           TEXT -> "t" + quoted
           BIN  -> "b" + lower-case hex
           BOOL -> "TRUE" | "FALSE"

children:  none    -> ""
           n keys  -> "{" key "=" dump(child) { ", " key "=" dump(child) } "}"
```

Keys are quoted with the same escape set as text. Children appear in insertion
order — order is normative, so a dump mismatch caused purely by ordering is a
real failure.

```
(1, 2)              ->  -{"1"=t"1", "2"=t"2"}
A=1; A[2]="x"; A    ->  t"1"{"2"=t"x"}
```

---

## Running

```
node js/bin/conformance.mjs                              [file…]
php  php/bin/conformance                                 [file…]
cpp/build/conformance                                    [file…]
lisp/bin/conformance                                     [file…]
PYTHONPATH=$PWD/python python3 python/bin/conformance.py [file…]
```

With no arguments each runs every `conformance/*.selt`. All exit non-zero on any
failure and print, for each, the case name, the expectation, and what was
actually produced. `tools/check.sh` runs the lot; `tools/impls.sh` is the roster
they come from.

## The files

`01`–`10` are grouped by language feature. Two more are grouped by *failure*
instead, because misuse is a surface of its own and organising it by feature
scattered it into a dozen places where nobody could see what was missing:

| File | Holds | Naming |
|---|---|---|
| `11-arity.selt` | every function, one argument below its minimum and one above its maximum | `arity.<function>.too-few` / `.too-many` |
| `12-misuse.selt` | wrong types, wrong values, wrong shapes — grouped by the `Args` accessor that rejects the argument | `mis.<accessor-or-area>.<detail>` |

Both pin `at line:col` on every case. For `11` that is the call's position; for
`12` it is the *argument's*, which is the innermost-failure promise in
`spec/errors.md` being held to. A new built-in lands with its two arity cases and
its type cases in the same change as its implementation.

## Adding cases

A new built-in lands together with its cases in the same change. When the two
hosts disagree, add the minimal case that reproduces it **before** fixing either
one — that case is the durable part of the fix.

Before adding a case, check that its source is not already in the suite under
another name: names are checked for uniqueness by the runner, sources are not,
and a duplicate pair survived in the suite for exactly that reason.

`26-evaluation-order.selt` pins what an optimiser must not change: the order in which a strict function's arguments are evaluated and then checked (SPEC 7.1), that a value yielded is not a snapshot (3.4), that `SORT`/`SORT_BY` plus `TAKE` evaluate keys before the count and still evaluate them for a count of zero (7.4), that FILTER steps fuse without reordering errors, that an error under an operator keeps the failing node's position (6.3), that a rewrite adds no evaluation depth (6.4), and that a caught failure leaves nothing behind. Where the spec does not decide an order (a binary operator's operands) there is deliberately no case.

`23-runtime-regressions.selt` covers record keys containing NUL, size arguments
beyond machine integer width, and depth/scope restoration after caught errors.
Go cache concurrency is covered separately by `go/sel/concurrency_test.go`;
`cd go && make test` and the Go unit lane in `tools/check.sh` use `go test -race`
to enable these tests and detect unsynchronized cache access.

`24-decimal-boundaries.selt` is **generated** (`tools/gen-decimal-cases.py`) from the
exact-rational oracle `tools/decimal-oracle-exact.py`: every operator and ROUND / CEIL /
FLOOR / TRUNC / POWER at the magnitudes the decimal cores were found wrong at (scale 18/19,
int64 / uint64 / int128 boundaries with both signs, remainders past 2^126, divisor spellings
1, 1.0, 0.1 and 1e-N, half-way ties, negative zero, the carry across the digit cap). Edit
the generator, not the file; `tools/gen-decimal-cases.py --check` fails when it is stale.

`25-value-ownership.selt` pins who copies and who aliases (`alias.*`, `struct.*`),
what a warm decimal or identity cache must not remember (`cache.*`), record
shapes of 14–20 fields with awkward keys (`record.shape.*`; the cases run in one
process, so a poisoned shape shows up in the cases after it) and the value-depth
cap for a target plus the value stored under it (`lim.assign-depth-target-*`).
Only what spec §3.4/§5.8/§5.9/§7.3 decide is pinned there: assignment, `,` and the
§7.3 aggregates copy what they collect, and every list a function returns is a new
container. Whether `LIST`/`RECORD` and the §7.4 structure functions copy their
elements is not pinned until the spec says.

`27-relational-edges.selt` covers the edges of the aggregates, the sorts, `BUCKET` and `LINK`: iteration over a snapshot of the source while the body mutates it, a scalar as a one-element list for the sorts, `TOP*` and `BUCKET`, direction and count evaluated even for an empty list, `TOP` equal to `TAKE` of `SORT` for every count, `BUCKET` group keys (index keys in two arguments, identity in three), `SELECT_COLS` with a repeated name, and equi-join keys against the per-pair path. Where the spec does not yet order mixed numeric-looking and other text, the cases assert only what any total order must satisfy (permutation independence, idempotence, `TOP` = `TAKE(SORT)`).

`29-text-binary-budgets.selt` pins the text and collection caps of SPEC §6.4 (16 777 216 code points / bytes, 1 000 000 children, `E_RANGE` at the node that builds the value, measured from lengths before allocation) at the cap, one past it, and for counts no machine integer holds, through `REPEAT`, `PADL`/`PADR`, `&`, `JOIN`, `REPLACE`, `RREPLACE`, `SPLIT`, `TO_HEX`, `ENCODE_BASE64`, `TO_UTF8`, `BTL`, `,` and `LINK`; and the binary/text boundary cases beside them (`LTB` of an empty list and of integral-but-scaled bytes, `PATH` with an empty path, `E_UTF8` and `E_BAD_ARG` diagnostics, 401-digit counts in `LEFT`/`SUBSTR`/`FIND`). The boundary cases build up to 16 MB of text each; that a *refusal* is cheap and survives a small machine is checked by `tools/check-budgets.sh`, which runs the worst requests one process each under a memory and time ceiling. Interpolation (`"{S}{S}"`) is capped through its `&` chain but has no case: which node it is positioned at is not stated by the spec.

`28-regex-portability.selt` holds what SPEC §7.8 decides about the portable regex subset, and holds every host to it: quantified anchors, class escapes as range endpoints, POSIX bracket forms (`[:`, `[.`, `[=`) anywhere in a class, PCRE verbs, the nullable-loop-body and optional-capture-in-a-loop rejections, the group-depth (200), group-count (1 000) and pattern-length (65 535) caps, counted repeats up to 65 535 with *matching* subjects (a FALSE-only case hides an engine that turned the repeat into "never matches"), literal patterns checked at compile time, `i` as the only flag, `RREPLACE`'s empty-match walk, and long subjects that must answer correctly rather than FALSE, 0 or unchanged text. Patterns are raw `'…'` literals because `{` in `"…"` is interpolation. A case that can crash a host (a 50 000-deep pattern) makes that host's whole run die: run it per case with `tools/check-regex-resources.py`'s bounded child processes. Catastrophic backtracking is in the next file.

`28b-regex-ambiguity.selt` is **generated** (`tools/gen-regex-ambiguity-cases.py`) from the lists in `tools/regex-ambiguity-ref.py`, the reference for SPEC §7.8's exponential-ambiguity refusal (a static rule over a position automaton). It pins 35 patterns that must be refused (`(a+)+$`, `(a|aa)+$`, `(.+)+x`, `(?:\d|\d\d)+$` …), the three pairs the `i` flag turns from accepted to refused, the ambiguity-budget boundaries (`(a|a)` ×8 accepted, ×9 refused; `a?` ×7 against ×8), structural errors the real parser now owns, and 46 patterns that must *not* be refused — real validation rules and unambiguous look-alikes (`(?:foo|foobar)*`, `^(?:ab|a)*$`, `(\d+,)+`) — whose match results come from Python's `re`. Patterns are literals and the subjects short, so it is compile-time validation being tested; the whole set is checked against the reference (`tools/regex-ambiguity-ref.py --self-check`, `--cases FILE`) before it is written, and `--check` on the generator fails if the file is stale. Running the reference alone does not need a host.
