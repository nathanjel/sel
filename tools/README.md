# The harness

Five layers, run together by `tools/check.sh`. Everything here iterates
`tools/impls.sh` rather than naming hosts, so a new implementation joins by
adding one entry there and providing the four entry points below.

```
tools/check.sh              everything
tools/check-docs.sh         every worked example in the documentation
tools/check-decimal.sh      every decimal core against Python's `decimal`
tools/e2e.sh                one rule set through every host API
tools/fuzz.sh               seeded differential fuzzing, N-way
```

Only the JS side owns generators: `gen-programs.mjs` (fuzz corpus),
`extract-docs.mjs` (documentation corpus) and `decimal-oracle.py` (Python) run
once and feed every implementation. A port never re-implements a generator, only
the four consumers.

---

## What an implementation must provide

| Role | Reads | Writes | Exit |
|---|---|---|---|
| `conformance [file…]` | `conformance/*.selt` | a human report | non-zero on any failure |
| `batch [--show] <corpus>` | a corpus file | one canonical line per program | 0 unless it cannot read the corpus |
| `e2e` | `examples/order-validation.sel` | the scenario report | 0 |
| `check-decimal <oracle>` | an oracle file | `<impl>: N cases, M mismatches` | non-zero on any mismatch |

All four run from the repository root and take paths relative to it.

---

## The canonical batch line

This is the cross-implementation comparison protocol. `batch` prints exactly one
line per program in the corpus, in order:

| Outcome | Line |
|---|---|
| a value | `dump()`, the canonical form in `conformance/README.md` |
| a `SelError` | `!CODE@line:col` |
| a crash in the host itself | `!HOST <class>: <message>` |

`--show` is for comparing against documentation only, never for differential
comparison — it is a deliberately lossy one-line rendering, and its newline
escape is not reversible (a value containing a real newline and one containing
the two characters `\` `n` render alike). The fuzzer uses the `dump()` form,
which is fully escaped. With that caveat, `--show` switches the value rendering
to the one `bin/sel` uses — bare text,
`TRUE`/`FALSE`, `bin:<hex>`, or the dump when the value has children — and drops
the position from errors, leaving `!CODE`. That is the form the documentation
writes, and it is what `check-docs.sh` compares against. Any newline in a
rendered value is escaped to `\n` so the one-line-per-program protocol holds even
when a program returns multi-line text.

A `!HOST` line is always a bug: it means the implementation crashed instead of
raising a `SelError`.

---

## The corpus format

Programs, one record each, so that no implementation needs a JSON parser — the
same reasoning as `conformance/README.md`. A line beginning `### ` starts a
record; every following line is source, verbatim, until the next marker.

```
### 1
A = 1; A + 2
### 2
LEFT("héllo", 3)
```

Reading it is five lines in any language. The text after `### ` is a label for
human reading only; records are matched positionally.

**The record is the joined lines with exactly one trailing newline removed.**
That sentence is normative for the readers, and it is fussier than it looks: a
trailing newline moves the position SEL reports for an end-of-input error, so a
reader that keeps one where another drops one produces a phantom disagreement
that looks like an interpreter bug. Three of the four readers got this wrong at
least once — PHP stripped two (PCRE's `$` matches before a final newline and the
replace is global), Lisp stripped none, and C++ swallowed a record's *leading*
blank line. A record may contain blank lines, including leading ones.

A program containing a line that itself begins with `### ` splits into two
records. Every reader does this identically, so it over-counts rather than
desynchronising, but do not write one.

Produced by `tools/gen-programs.mjs <count> <seed>` and by
`tools/extract-docs.mjs <file…>`, which also writes the expected `--show` output
next to the corpus.

---

## The decimal oracle format

One case per line, four `|`-separated fields — none of which can contain a `|`,
since they are all decimal strings or operator names.

```
op|a|b|want
+|1.50|2.50|4.00
/|1|3|0.3333333333
round|2.5|0|3
```

Operators: `+ - * / % cmp round floor ceil trunc`. For `round`, `b` is the
target scale; for `floor`, `ceil` and `trunc`, `b` is unused. `want` is the
implementation's `format()` output, or `THREW <CODE>` when the case is expected
to fail.

Produced by `tools/decimal-oracle.py <count> <seed>`. Python's `decimal` shares
no code with any SEL implementation, which is the point: the SEL cores were
written from one spec by one hand, so they would agree with each other while
being wrong. This is the third opinion.
