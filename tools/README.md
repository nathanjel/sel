# The harness

Five layers, run together by `tools/check.sh`. Everything here iterates
`tools/impls.sh` rather than naming hosts, so a new implementation joins by
adding one entry there and providing the five entry points below.

```
tools/check.sh              everything
tools/check-docs.sh         every worked example in the documentation
tools/check-decimal.sh      every decimal core against Python's `decimal`
tools/e2e.sh                one rule set through every host API
tools/check-api.sh          the same API probes through every host binding
tools/check-version.sh      every manifest declares the same version
cd cpp && make asan         the C++ suite under the address and leak sanitizers
tools/fuzz.sh               seeded differential fuzzing, N-way
tools/check-sql-map.sh      the dialect map, regenerated and diffed
tools/check-sql-docs.sh     the design document quotes cases that run
tools/check-sql-oracle.sh   translated SQL against a real database
tools/fuzz-sql.sh           seeded SQL differential fuzzing against a database
```

Only the JS side owns generators: `gen-programs.mjs` (fuzz corpus),
`extract-docs.mjs` (documentation corpus) and `decimal-oracle.py` (Python) run
once and feed every implementation. A port never re-implements a generator, only
the five consumers.

`decimal-oracle.py` being Python is worth one sentence now that a Python host
exists: `python/sel/decimal.py` deliberately does **not** use the `decimal`
module, so the oracle remains a genuinely independent opinion for that host
rather than a comparison of the standard library with itself.

---

## What an implementation must provide

| Role | Reads | Writes | Exit |
|---|---|---|---|
| `conformance [file…]` | `conformance/*.selt` | a human report | non-zero on any failure |
| `batch [--show] <corpus>` | a corpus file | one canonical line per program | 0 unless it cannot read the corpus |
| `e2e` | `examples/order-validation.sel` | the scenario report | 0 |
| `api` | nothing | the API parity report, one `NN name = value` line per probe | 0 |
| `check-decimal <oracle>` | an oracle file | `<impl>: N cases, M mismatches` | non-zero on any mismatch |
| `sql [filter…]` | `sql/cases/*.sqlt` | `N passed, M failed` | non-zero on any failure; **0 and silent** for a host with no SQL layer |
| `oracle [mode]` | `sql/oracle/*` | a per-mode agreement report | non-zero on any disagreement; **0 with a skip line** when no DSN is set |
| `sqldoc [file.md…]` | `docs/SQL-TRANSLATION.md`, `sql/cases/*.sqlt` | `N quote a case, M wrong` | non-zero on any mismatch, and on finding no blocks |

The first five are required. `sql` and `oracle` are optional in the same way
`unit` is: a host with no SQL layer succeeds silently and the harness moves on.

All of them run from the repository root and take paths relative to it.

`oracle` needs a database and finds it in the environment, named for the dialect:

```
SEL_SQL_MARIADB_DSN='mysql:unix_socket=/var/lib/mysql/mysql.sock;dbname=sel_oracle;charset=utf8mb4'
SEL_SQL_MARIADB_USER=you
SEL_SQL_MARIADB_PASS=
```

With no DSN it prints a skip and succeeds, so a fresh clone stays green. The
named schema must exist and must be disposable: the row oracle drops and
recreates its tables on every run. See `sql/oracle/README.md`.

| Role | js | php | cpp | lisp | python |
|---|---|---|---|---|---|
| `conformance` | `js/bin/conformance.mjs` | `php/bin/conformance` | `cpp/build/conformance` | `lisp/bin/conformance` | `python/bin/conformance.py` |
| `batch` | `tools/run-batch.mjs` | `tools/run-batch.php` | `cpp/build/batch` | `lisp/bin/batch` | `python/bin/batch.py` |
| `e2e` | `examples/e2e.mjs` | `examples/e2e.php` | `cpp/build/e2e` | `lisp/bin/e2e` | `examples/e2e.py` |
| `api` | `tools/api.mjs` | `tools/api.php` | `cpp/build/api` | `lisp/bin/api` | `python/bin/api.py` |
| `check-decimal` | `tools/check-decimal.mjs` | `tools/check-decimal.php` | `cpp/build/check-decimal` | `lisp/bin/check-decimal` | `python/bin/check-decimal.py` |
| `sql` | — | `php/bin/sqlt` | — | — | M6 |
| `oracle` | — | `php/bin/sqlo` | — | — | M6 |
| `sqldoc` | — | `php/bin/sqldoc` | — | — | M6 |

Two implementations in `tools/impls.sh` are the same code reached a second way:
`js-bundle` runs `dist/sel.mjs`, and `python-wheel` runs the built wheel from a
venv. Both are guarded on being newer than the sources they were built from, and
both exist to catch the failures that only packaging can produce.

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
that looks like an interpreter bug. Three of the first four readers got this
wrong at least once — PHP stripped two (PCRE's `$` matches before a final
newline and the replace is global), Lisp stripped none, and C++ swallowed a
record's *leading* blank line. A record may contain blank lines, including
leading ones.

Python has two ways to join the list. `str.splitlines()` also breaks on `\v`,
`\f`, `\x1c`-`\x1e`, U+0085, U+2028 and U+2029, so it would split records that
contain any of them — and the suite contains such characters deliberately.
`str.rstrip('\n')` strips *every* trailing newline rather than exactly one.
`python/bin/batch.py` uses `.split('\n')` and removes one, and says so.

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
