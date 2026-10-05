# The harness

The layers run together by `tools/check.sh`. Everything here iterates
`tools/impls.sh` rather than naming hosts, so a new implementation joins by
adding an arm to each role function there (`tools/check-registry.sh` fails until
every role has one) and providing the entry points below.

```
tools/check.sh              everything, side by side (SEL_JOBS, SEL_PHP_JOBS: see impls.sh)
tools/check-docs.sh         every worked example in the documentation
tools/check-decimal.sh      every decimal core against Python's `decimal`
tools/e2e.sh                one rule set through every host API
tools/check-api.sh          the same API probes through every host binding
tools/check-version.sh      every manifest declares the same version
cd cpp && make asan         the C++ suite under the address and leak sanitizers
tools/check-cli-source.sh   every `sel` CLI: source bytes and the CLI contract (docs/usage/repl.md)
tools/check-corpus-bytes.sh every batch runner on byte fixtures (CR, CRLF, final blank line)
tools/check-registry.sh     every roster configuration has an arm in every role of impls.sh
tools/check-budgets.sh      output/work budgets refused cheaply, per host, under ceilings
tools/check-sql-budgets.sh  translator work/depth budgets, per host, under ceilings
tools/check-regex-resources.py   regex resource behaviour, per host, under ceilings
tools/check-regex-ambiguity-diff.sh  every host's regex validator vs the reference
tools/check-host-examples.sh     the six top-level host programs in examples/ run cleanly
tools/check-generators.sh   every gen-* refuses an unknown argument and writes nothing for --help
tools/check-ref-fragments.sh     the fn-* reference fragments run against their cases
tools/fuzz.sh               seeded differential fuzzing, N-way
tools/check-sql-map.sh      the dialect map, regenerated and diffed
tools/check-sql-docs.sh     the design document quotes cases that run
tools/mutate-sql.sh         break the SQL layer on purpose; the checks must notice
tools/check-sql-oracle.sh   translated SQL against a real database
tools/oracle-db.sh          starts pinned throwaway servers, runs the above, removes them
tools/fuzz-sql.sh           seeded SQL differential fuzzing against a database
tools/scale-test/benchmark_php_memory.php
                            PHP-only shaped-ingestion, materialization, join,
                            shape-cache, and memory probes
```

## Concurrency knobs

The gate is a pool of leaf commands, each holding one flock(1) slot
(`tools/impls.sh`, slot files under `$TMPDIR/sel-slots-<uid>`), so nested tools
share one bound instead of each adding their own.

| Knob | Default | Meaning |
|---|---|---|
| `SEL_JOBS` | 3/4 of the hardware threads (12 of 16) when the one-minute load average is below half the threads at start, otherwise half (8) | leaf commands at once. The gate exports the value, so everything it starts agrees; run standalone, each tool decides for itself. |
| `SEL_PHP_JOBS` | half of `SEL_JOBS` | how many of them may be PHP (a PHP leaf is single-threaded; on a box whose `php` is a Docker wrapper, lower it, each call is a container start) |
| `SEL_MUTATE_JOBS` | `tools/mutate-sql.sh`: `SEL_JOBS`; under the gate: two thirds of it | mutations graded side by side. The cap exists so the lane cannot hold every slot and starve the gate's shorter layers. |
| `SEL_DB_LOCK` | set by the gate (`$LOGS/db.lock`) | the database layers share one schema, so they serialise on this lock — taken for the checks that touch it (the oracle, the oracle half of the SQL fuzz, each mutation-lane oracle check), never for a whole lane. Always taken before a slot. |
| `SEL_SLOT_DIR` | `$TMPDIR/sel-slots-<uid>` | where the slot locks live; set it to isolate a run from other SEL tools on the box |

`tools/mutate-sql.sh` grades each mutation with the mutated host's OWN checks
first (a mutation in `js/src/sql` runs the JS suites before anything else) and
the remaining hosts' after; a mutation counts as caught if any check fails, so
the order changes only what a mutation costs, never its verdict. The lane copies
the tree once (the seed), builds the C++ SQL tools in it once in parallel, runs
the baseline there, and copies the seed, objects included, per mutation, so a
mutation of one C++ translation unit recompiles that unit, not the library. A
mutation to a C++ header gets a clean build.

The generators of committed artifacts (`gen-*.mjs`, `gen-*.py`) share one
command line and one write-or-check loop — `tools/gen-lib.mjs` and
`tools/gen_lib.py`: no argument writes (only the files whose content changed),
`--check` compares and writes nothing, `--help` prints the usage, anything else
is exit 2. The Node side also takes every host language's string-literal
escaper from `gen-lib.mjs`, so no generator carries its own.

Only the JS side owns the corpus generators: `gen-programs.mjs` (fuzz corpus),
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
| `sqlreplay` | nothing | the shipped map rebuilt through the public registration API, and diffed against itself | 0 unless the API cannot express the map |
| `sqlfuzz <corpus> [dialect]` | a corpus file | one canonical line per program, three `\|\|`-separated lanes: `translate` (the inline SQL, the `params` SQL and the bound values, or a refusal), `translate_statement` likewise, and `plan_hybrid` (the classification, then the prefix statement in `params` mode); every runner binds the same ORDERS and CUSTOMERS relations, which `gen-programs.mjs --sql` writes pipelines over (the AMOUNT field declares no table, so a join must qualify it by the relation's alias — SEL-0042) | 0 unless it cannot read the corpus |
| `e2e` | `examples/order-validation.sel` | the scenario report | 0 |
| `api` | nothing | the API parity report, one `NN name = value` line per probe | 0 |
| `sqlapi` | nothing | the planner's contract, one `NN name = value` line per probe (tools/check-sqlapi.sh); **0 and silent** for a host with no SQL layer | 0 |
| `check-decimal <oracle>` | an oracle file | `<impl>: N cases, M mismatches` | non-zero on any mismatch |
| `sql [filter…]` | `sql/cases/*.sqlt` | `N passed, M failed` | non-zero on any failure; **0 and silent** for a host with no SQL layer |
| `oracle [mode]` | `sql/oracle/*` | a per-mode agreement report | non-zero on any disagreement; **0 with a skip line** when no DSN is set |
| `regex_verdict [i]` | one pattern per stdin line | `A` (accepted) or `R` (refused with `E_REGEX_SYNTAX`) per line | 0 |
| `sqldoc [file.md…]` | `docs/internals/sql-translation.md`, `sql/cases/*.sqlt` | `N quote a case, M wrong` | non-zero on any mismatch, and on finding no blocks |

The first five are required. `sql` and `oracle` are optional in the same way
`unit` is: a host with no SQL layer succeeds silently and the harness moves on.

All of them run from the repository root and take paths relative to it.

`oracle` needs a database and finds it in the environment, named for the dialect:

```
SEL_SQL_MARIADB_DSN='mysql:unix_socket=/var/lib/mysql/mysql.sock;dbname=sel_oracle;charset=utf8mb4'
SEL_SQL_MARIADB_USER=you
SEL_SQL_MARIADB_PASS=
SEL_SQL_SQLITE_DSN='sqlite::memory:'
SEL_SQL_MYSQL_DSN='mysql:host=127.0.0.1;port=13306;dbname=sel_oracle;charset=utf8mb4'
SEL_SQL_MYSQL_USER=root
```

There is no MySQL on most machines, and MariaDB's `mysql`/`mysqld` binaries are
compatibility symlinks rather than the thing itself. A container is the usual
way to get a real one:

```
docker run -d --name sel-mysql -e MYSQL_ALLOW_EMPTY_PASSWORD=1 \
  -e MYSQL_DATABASE=sel_oracle -p 13306:3306 mysql:8.4
docker run -d --name sel-pg -e POSTGRES_PASSWORD=sel \
  -e POSTGRES_DB=sel_oracle -p 15432:5432 postgres:17
```

One per target dialect, and the oracle runs every target it has a DSN for. With
no DSN a dialect prints a skip and succeeds, so a fresh clone stays green — and
SQLite needs nothing installed beyond `pdo_sqlite`, so in practice there is
always at least one server to ask.

A named schema must exist and must be disposable: the row oracle drops and
recreates its tables on every run. See `sql/oracle/README.md`.

| Role | js | php | cpp | lisp | python |
|---|---|---|---|---|---|
| `conformance` | `js/bin/conformance.mjs` | `php/bin/conformance` | `cpp/build/conformance` | `lisp/bin/conformance` | `python/bin/conformance.py` |
| `batch` | `tools/run-batch.mjs` | `tools/run-batch.php` | `cpp/build/batch` | `lisp/bin/batch` | `python/bin/batch.py` |
| `e2e` | `examples/e2e.mjs` | `examples/e2e.php` | `cpp/build/e2e` | `lisp/bin/e2e` | `examples/e2e.py` |
| `api` | `tools/api.mjs` | `tools/api.php` | `cpp/build/api` | `lisp/bin/api` | `python/bin/api.py` |
| `check-decimal` | `tools/check-decimal.mjs` | `tools/check-decimal.php` | `cpp/build/check-decimal` | `lisp/bin/check-decimal` | `python/bin/check-decimal.py` |
| `sql` | `js/bin/sqlt.mjs` | `php/bin/sqlt` | — | — | `python/bin/sqlt` |
| `oracle` | — | `php/bin/sqlo` | — | — | — |
| `sqldoc` | — | `php/bin/sqldoc` | — | — | — |

`oracle` and `sqldoc` are PHP-only by design rather than unfinished: both ask
about `sql/dialects/*.json` and `sql/cases/*.sqlt`, which are shared data every
host consumes unchanged, so a second copy would ask one server the same question
twice. See docs/internals/sql-translation.md §14, M7.

Three implementations in `tools/impls.sh` are the same code reached a second way:
`js-bundle` runs `dist/sel.mjs`, `js-bundle-min` runs `dist/sel.min.mjs`, and
`python-wheel` runs the built wheel from a venv. All three are guarded on being
newer than the sources they were built from, and all three exist to catch the
failures that only packaging can produce — a minifier that renames something it
should not have is exactly that failure, and `dist/sel.min.mjs` is published as
package.json's `"./bundle.min"`, so it is graded rather than merely shipped.

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

**The file is bytes, decoded as UTF-8 with no newline translation.** A CR is
program text wherever it appears, including immediately before the LF that ends
a line: the newline removed is one `\n`, never a `\r` and never a second `\n`.
That rules out every line-reading convenience that normalises line ends —
Python's universal newlines (`open(path)` without `newline=''`), Go's
`bufio.ScanLines`, Rust's `BufRead::lines()` — all of which strip the CR before
an LF. `tools/check-corpus-bytes.sh` holds every host's `batch` runner to this
with the byte fixtures in `tools/fixtures/corpus/`: CR and CRLF inside a literal
and at a line end, a record with leading and inner blank lines, and a final
record that ends in a blank line, at its newline, and at end of file.

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
