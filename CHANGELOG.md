# Changelog

SEL's releases, newest first. This file starts at 0.6.0; the entries below it
were written afterwards, from the commits, and are shorter for it.

Six manifests and `python/sel/__init__.py` carry the version, and so does the
top heading here — `tools/check-version.sh` relates all eight, so a release
whose notes were never written fails the check before the tag is cut.

Each entry ends with the three lanes that gate a release: conformance cases
(every host runs all of them), SQL translation cases, and mutations caught.

## 0.6.1 — 2026-09-09

A release closing §4.1a of the kind warrant, shipping TypeScript typings,
and standardizing dialect representations.

  - **Function arguments read as numbers receive `numericGuard` (§4.1a).**
    Previously, numeric guards were applied in `binary()` and `unary()` while
    function calls reached neither. An undeclared or TEXT column passed to a
    numeric function argument (such as `ABS(T)`, `ROUND(T, 2)`, or `LEFT("abc", T)`)
    reached the database unguarded. On SQLite, MariaDB, and MySQL, passing
    non-numeric text returned 0 rather than raising `E_NOT_NUM`, so `ABS(T) == 0`
    evaluated to TRUE for every row.
    Numeric argument positions are now tracked across all built-in functions.
    Arguments in numeric positions not known to be NUM are wrapped in
    `numericGuard` on MariaDB, MySQL, and PostgreSQL; on dialects without a
    numeric guard (SQLite, ANSI), translation safely refuses with
    `E_SQL_UNSUPPORTED`.

  - **Standardized ANSI `binaryCast`.**
    ISO/IEC 9075-2 specifies `BLOB` rather than MySQL's `BINARY`. `ansi.json`
    now specifies `CAST({0} AS BLOB)` for `binaryCast`, and `mysql-family.json`
    explicitly defines `CAST({0} AS BINARY)`.

  - **SQLite TRIM family emits single-line SQL.**
    SQLite's `TRIM`, `LTRIM`, and `RTRIM` templates now concatenate character
    codes via `' ' || char(9) || char(13) || char(10)` rather than embedding
    raw tab, CR, and newline literals inside JSON string templates, preventing
    multi-line query emissions while preserving exact whitespace stripping.

  - **Cross-host Map Replay comparison.**
    `tools/check.sh` now asserts that all five hosts produce byte-identical
    summary output (rebuilding registrations and lookups compared) during
    sql map replay.

  - **TypeScript definitions shipped.**
    `js/src/sel.d.ts`, `js/src/sql.d.ts`, and `js/src/sql/index.d.ts` are now
    provided and declared via `"types": "./js/src/sel.d.ts"` in `package.json`.

  - **Python integration example.**
    `examples/integration-python.py` added to mirror the JS and PHP worked
    integration examples with byte-identical output.

conformance 631 · sql cases 454 · mutations 161

## 0.6.0 — 2026-09-09

**If you registered your own dialect with `extends: 'ansi'` and overrode
nothing, your emitted SQL changes in this release.** `ansi` now says what the
standard says: `CAST(x AS CHARACTER VARYING)` rather than `AS CHAR`,
`CAST(x AS NUMERIC)` rather than `AS DECIMAL(38,10)`, `CHAR_LENGTH`,
`TRIM(BOTH FROM x)`, `SUBSTRING(x FROM n FOR m)`, `||` for concatenation, and
`COLLATE UCS_BASIC` where the old map collated with whatever the server felt
like. The old output was MySQL wearing the word ANSI, and on a conformant
server it produced matches SEL refuses — so this is a correction, not a
preference. Run your suite against it.

The four shipped dialects — `mariadb`, `mysql`, `postgresql`, `sqlite` — emit
byte-identical SQL to 0.5.0. Not one expected string in `sql/cases` moved;
`mysql-family` now states the eight keys it used to inherit, so making `ansi`
standard could not drag it along.

  - **`ansi` is checked, with no ANSI server to check against.** A dialect
    registered only inside the oracle and the case runner, `ansi-probe`,
    extends `ansi` and runs its emitted SQL against PostgreSQL — the closest
    conformant server there is. Where PostgreSQL is stricter than the standard
    the probe still refuses honestly rather than passing quietly. `sql/MAP.md`
    §1.1 has the measured table of what `ansi` used to claim and what a
    conformant server actually did with it.

  - **The guard evaluates a raw binding twice, and now says so.**
    `numericGuard` names its operand in both the test and the value, so a
    binding whose SQL is a raw expression — a subquery, a function call — is
    evaluated twice per row. It has always done this. `docs/SQL-KINDS.md` §6
    now states it as an exclusion and gives the fix: name the expression once
    in a derived table or CTE and bind the column, `SELECT a.b AS c FROM x`,
    then let SEL use `c`.

  - **One reader lied about whitespace.** JavaScript's `String.prototype.trim`
    strips U+FEFF, so the JS conformance reader silently deleted a byte-order
    mark from a case body while the other four kept it — five readers, five
    slightly different files. All five now trim exactly the four characters
    SEL calls whitespace (space, tab, CR, LF) and nothing else, and
    `conformance/README.md` states that as a normative rule rather than
    leaving it to each host's standard library.

  - **The outliers nothing looked at.** Cases for the corners a language
    usually dies on: nesting at `MAX_DEPTH`, `NONE` reaching a scalar
    position by every route, the ASCII-only edge of `UPPER`/`LOWER`,
    short-circuit in `ANY`/`ALL`, and the numeric-looking strings that are
    text because in SEL numbers are text.

  - **The caveat gate could not see `ansi`.** A caveated map entry must be
    pinned by a string in a case file, for every target — but `ansi` is not a
    target, so entries reachable only through it were never pinned, and a
    mutation to one of them survived. The gate now registers the probe before
    it walks the dialects, and it immediately found two more unpinned entries.

  - **The sdist could ship a virtualenv.** Hatchling's `include` patterns are
    gitignore syntax, and an unanchored `python/` matches that directory at any
    depth — so it swept in `python/.venv-wheel/`, the throwaway venv
    `PACKAGING.md` tells you to build in order to verify the wheel, and in a
    checkout with a git worktree under `.claude/`, a second copy of the whole
    repository. Every pattern is anchored now and the venv and `__pycache__`
    are excluded by name. The wheel was never affected, only the sdist.

  - **Two holes in the release gate itself.** `tools/impls.sh` checks that the
    JS bundle and the installed Python wheel are newer than the sources they
    were built from, and did not check the same for C++ — so a `cpp/build`
    left over from an earlier branch passed 631 conformance cases and
    disagreed with the other four hosts only in the SQL fuzz, which reads
    exactly like a translator bug and is nothing of the kind. (`cmake --build`
    builds the installable library; the harness binaries come from the
    Makefile, so a plausible build command leaves them untouched.) And
    `tools/fuzz-sql.sh` ended its per-dialect summary with a literal
    `0 disagreements` printed immediately after printing a diff — the lane
    failed correctly, but the sentence was false. It counts them now.

Upgrading from 0.4.x: you also meet everything 0.5.0 refuses — see below.

conformance 631 · sql cases 450 · mutations 161

## 0.5.0 — 2026-09-08

The kind warrant: *SQL never reports a match for a row SEL would have
refused*. Reported by the first external user of the translation layer, and
closing it takes functionality away. A minor bump and not a patch, because
programs that translated in 0.4.2 refuse now.

What refuses that did not: an undeclared column in a boolean position
(`E_SQL_SHAPE` — in the MySQL family a boolean *is* a TINYINT, so no dialect
can be asked); a TEXT or undeclared column in a numeric position on `sqlite`
and `ansi` (`E_SQL_UNSUPPORTED` — neither can ask whether a value is a
number); and a registered dialect whose `numericGuard` disagrees with its
`funcs.ISNUM`, which used to fail silently. Declare `type: 'BOOL'` or
`type: 'NUM'` to buy the old behaviour back where the column really is one.

What emits differently: on `mariadb`, `mysql` and `postgresql` an uncertain
numeric operand is wrapped `CASE WHEN <ISNUM> THEN <cast> ELSE NULL END`, so a
row holding `'25/298'` contributes NULL instead of 25. It costs the index on
that column; declaring `NUM` returns the unguarded path.

conformance 621 · sql cases 421 · mutations 160

## 0.4.2 — 2026-09-08

Why `postgresql`'s UTF8 is not the key `mysql-family` just got, written down
where the map can be read. The artifact generators stop rewriting files whose
content did not change.

conformance 621 · sql cases 391 · mutations 115

## 0.4.1 — 2026-09-08

`textCharset` becomes a lexical key of the dialect map rather than a spelling
choice. Worked examples in seven categories, in all five languages, with a
lane that diffs them against each other. How to derive the dialect from the
connection rather than write it down.

conformance 621 · sql cases 391 · mutations 115

## 0.4.0 — 2026-09-07

The SEL→SQL translation layer, in all five hosts, over 118 commits: the
dialect map as data with one place that defines it, the translator, the
aggregates, the oracle harness that runs generated SQL against real servers,
and the mutation lane.

conformance 621 · sql cases 390 · mutations 114

## 0.3.0 — 2026-08-27

Python joins as the fifth host, and major bugfixes across the others that
having a fifth reading of the spec turned up.

*(A stray tag named `v` points at this release's tag object. Harmless, left
alone.)*

conformance 587

## 0.1.4 — 2026-08-17

A clean release tag. `CMakeUserPresets.json` stops being tracked.

conformance 316

## 0.1.3 — 2026-08-17

Fix the Conan recipe, which had never been run.

## 0.1.2 — 2026-08-17

Host API parity — predicates, kind constants, `size()`, and a harness that
checks the four hosts expose the same surface.

## 0.1.1 — 2026-08-17

Single-file JS bundle.

## 0.1.0 — 2026-08-17

First tag. Four hosts — JavaScript, PHP, C++ and Common Lisp — against one
conformance corpus. No SQL layer yet.

conformance 316

*(0.2 was never tagged.)*
