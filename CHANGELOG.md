# Changelog

SEL's releases, newest first. This file starts at 0.6.0; the entries below it
were written afterwards, from the commits, and are shorter for it.

Six manifests and `python/sel/__init__.py` carry the version, and so does the
top heading here — `tools/check-version.sh` relates all eight, so a release
whose notes were never written fails the check before the tag is cut.

Each entry ends with the three lanes that gate a release: conformance cases
(every host runs all of them), SQL translation cases, and mutations caught.

## 0.7.4 — 2026-09-09

Optimizer-transparent existential precondition subqueries (`skel.prefilter`) without `IS TRUE` wrappers, enabling composite B-tree index seeks and semijoin decorrelation on MariaDB and MySQL.

  - **First-class `skel.prefilter` in the SQL dialect map.**
    - Adds `prefilter: ['from', 'corr', 'body']` to `skel` in `sql/dialects/ansi.json` (`EXISTS (SELECT 1 FROM {from} WHERE {corr} AND {body})`).
    - Omits the three-valued logic fold (`IS TRUE`) from the coarse precondition subquery emitted for separate relation prefilters (`prefilter: 'separate'`).
    - Eliminates the root `Item_func_istrue` AST node that previously prevented MariaDB/MySQL range optimizers from recognizing composite index keys (`(fname, value, cmsid)`) and caused subquery decorrelation to fail. Live query plans now successfully decorrelate into `LooseScan` semijoins driven by multi-part constant index seeks (`const,const`), reducing execution time to baseline (~1.30× legacy).
    - Preserves `({body}) IS TRUE` in the authoritative aggregate subquery (`skel.any`) where strict two-valued SEL semantics are enforced.
  - **Cross-runtime parity & test coverage.**
    - Implemented uniformly across PHP, JavaScript, Python, C++23, and Common Lisp.
    - Updated MariaDB separate prefilter test cases in `sql/cases/22-sargable-bindings.sqlt`, passing across all 5 host runtimes.

conformance 720 · sql cases 485 · mutations 161

## 0.7.3 — 2026-09-09

Separately planable necessary prefilters for relation bindings (`prefilter: 'separate' | 'inline'`), enabling composite B-tree index acceleration in MariaDB/MySQL aggregate subqueries.

  - **Separately planable necessary prefilters (`prefilter`, `splitSargable`).**
    - Adds `prefilter: 'separate' | 'inline'` (with boolean aliases `true` -> `'separate'`, `false` -> `'inline'`, and `splitSargable: true` -> `'separate'`) to both relation bindings and column/raw bindings across PHP, JavaScript, Python, C++23, and Common Lisp.
    - Resolves severe query plan degradations (up to 118.91× slowdown) on MariaDB and MySQL where residual collation expressions (`COLLATE utf8mb4_bin`) inside correlated subqueries prevent the optimizer from utilizing composite indexes on EAV tables.
    - In `ANY` aggregate subqueries on engines where `sargablePrefilter: true` (MariaDB, MySQL) when `prefilter === 'separate'` (on relation or column), SEL emits two sibling `EXISTS` subqueries conjoined by `AND`:
      `(EXISTS (SELECT 1 FROM rel WHERE corr AND coarse_prefilter IS TRUE) AND EXISTS (SELECT 1 FROM rel WHERE corr AND body IS TRUE))`
    - The coarse precondition subquery retains clean, uncast conditions and conjoins exact conditions (`g.fname = 'f_group' AND g.value = 'news'`), enabling MariaDB/MySQL optimizers to perform composite index range scans on `(fname, value, cmsid)` to dramatically restrict candidate rows before evaluating collation-sensitive residuals.
    - On PostgreSQL and SQLite (`sargablePrefilter: false`), sargable equality produces bare equality without coarse/residual splitting, emitting a single clean `EXISTS` subquery without duplication.
    - Relation-level `prefilter: 'inline'` overrides column-level `prefilter: 'separate'`, keeping all checks in a single subquery.
  - **Comprehensive test coverage & cross-runtime verification.**
    - Adds 6 new test cases across MariaDB, PostgreSQL, and SQLite in `sql/cases/22-sargable-bindings.sqlt`, expanding the SQL test suite to 485 cases.
    - 100% verified across all 5 host implementations (PHP, JavaScript, Python, C++23, Common Lisp).

conformance 720 · sql cases 485 · mutations 161

## 0.7.2 — 2026-09-09

Dialect map driven `sargablePrefilter` lexical configuration, enabling custom and derived SQL dialects extending MariaDB/MySQL to inherit sargable index prefilters.

  - **Architectural `sargablePrefilter` lexical configuration.**
    - Moves the engine-specific decision for emitting coarse equality prefilters (`col = 'val' AND ...`) from hardcoded dialect name string checks into the SQL dialect map (`sql/dialects/*.json`).
    - Configures `"sargablePrefilter": "true"` in `mysql-family.json` and `"false"` in `ansi.json`.
    - Custom and derived dialects extending MariaDB or MySQL (e.g. `Map::defineDialect('cms-mariadb', ['extends' => 'mariadb'])`) now properly inherit `sargablePrefilter: true` via standard dialect inheritance across all host implementations (PHP, JavaScript, Python, C++23, Common Lisp).
  - **Test coverage & dialect inheritance verification.**
    - Adds `bind.sargable.derived-dialect` to `sql/cases/22-sargable-bindings.sqlt` verifying that custom dialects extending `mariadb` inherit index-sargable prefilters.
    - SQL test suite expanded to 479 cases, passing across all 5 host implementations.

conformance 720 · sql cases 479 · mutations 161

## 0.7.1 — 2026-09-09

Index-sargable SQL bindings and numeric guard controls for relational database query optimizers, addressing 54×–102× execution regressions on indexed production tables.

  - **Index-sargable SQL bindings (`exact`, `sargable`, `collation`).**
    - Adds database-agnostic binding metadata parameters across all 5 host languages: `exact: bool = false`, `sargable: bool = false`, `guard: bool = false`, and `collation: ?string = null`.
    - `exact: true` skips defensive `CAST(... AS CHAR)` and `COLLATE` wrapping for string equality and ordering (`$==`, `$!=`, `$<`, `$<=`, `$>`, `$>=`) and `IN ("a", "b")` literal list expansions, restoring B-tree index seek and range scans across MariaDB, MySQL, PostgreSQL, and SQLite. Resolves 54×–102× query plan regressions on indexed production tables.
    - `sargable: true` emits a coarse equality prefilter combined with the exact binary check on MariaDB and MySQL (`((col = 'val') AND (CAST(col AS CHAR) COLLATE utf8mb4_bin = CAST('val' AS CHAR) COLLATE utf8mb4_bin))`), and clean bare equality on PostgreSQL and SQLite where text equality is already exact by default.
  - **Numeric guard control on EAV columns (`guard`).**
    - `guard: true` forces safe `numericGuard` evaluation on dirty EAV string columns even when declared `NUM`, preventing MariaDB error 1292 (`Truncated incorrect DOUBLE value`).
  - **Test coverage & cross-host parity.**
    - Adds 16 new test cases across all dialects in `sql/cases/22-sargable-bindings.sqlt`, expanding SQL test suite to 478 cases.
    - 100% verified across JavaScript, PHP, Python, C++23, and Common Lisp, with live database oracle validation on MariaDB 11.8, MySQL 8.4, PostgreSQL 17, and SQLite 3.51 (161 caught mutations, 0 survived).

conformance 720 · sql cases 478 · mutations 161

## 0.7.0 — 2026-09-09

Strategic release introducing first-class `NULL` semantics, loud refusal (`E_NULL`)
on unhandled operations, null and vacuous coalescing (`??`, `???`), safe container
navigation (`GET`, `PATH`), presence predicates (`IS_NULL`, `IS_NOT_NULL`, `IS_BLANK`,
`IS_PRESENT`), and comprehensive SQL pushdown verified against live database engines.

  - **First-class `NULL` semantics and loud refusal (`E_NULL`).**
    `NULL` is now a distinct absence value (`NONE` with no scalar and no children).
    SEL strictly refuses silent coercion: arithmetic (`NULL + 1`), string operations
    (`UPPER(NULL)`, `NULL & "x"`), and comparisons (`NULL == 0`, `NULL $== ""`, `NULL < 5`)
    all fail loudly with `E_NULL` instead of quietly evaluating to 0, empty text, or false.
    `EQL` structurally checks nullness: `NULL EQL NULL` is TRUE, and `NULL EQL ""` is FALSE.

  - **Coalescing operators `??` and `???`.**
    - `A ?? B` (Null coalescing): evaluates `A`; if `A` is `NULL` or encounters a missing
      key / undefined variable (`E_NO_KEY`, `E_UNDEF_VAR`), evaluates and returns `B`.
    - `A ??? B` (Vacuous / data-invariant coalescing): evaluates `A`; if `A` is vacuous
      (`NULL`, missing, empty text `""`, whitespace-only text, or empty container),
      evaluates and returns `B`. Right-associative with binding power 9.

  - **Safe container navigation (`GET`, `PATH`).**
    - `GET(container, key [, default])`: safe key retrieval from maps and lists. If the key
      is missing or the container is non-indexable/NULL, returns `default` (or `NULL` if omitted),
      suppressing `E_NO_KEY` and `E_UNDEF_VAR`.
    - `PATH(container, path [, default])`: safe multi-step navigation along a dot/slash-separated
      string (e.g. `"order.customer.name"`) or list of keys. Returns `default` (or `NULL` if omitted)
      if any segment is missing or not a container.

  - **Null and blank inspection functions.**
    - `IS_NULL(x)`: returns TRUE if `x` is `NULL`, FALSE otherwise.
    - `IS_NOT_NULL(x)`: returns FALSE if `x` is `NULL`, TRUE otherwise.
    - `COALESCE(v1, v2, ...)`: variadic, returns the first non-NULL argument (or NULL if all NULL).
    - `IS_BLANK(x)`: returns TRUE if `x` is NULL, empty string `""`, or only whitespace (`[ \t\r\n]`).
    - `IS_PRESENT(x)`: returns TRUE if `x` is not blank (not NULL and has non-whitespace characters).

  - **SQL pushdown & database oracle validation.**
    - All null operations and functions (`??`, `???`, `IS_NULL`, `IS_NOT_NULL`, `COALESCE`,
      `IS_BLANK`, `IS_PRESENT`) translate directly to SQL expressions across MariaDB, MySQL,
      PostgreSQL, and SQLite.
    - PostgreSQL strictly types parameters and text functions using `{textCast:0}` to prevent
      prepared statement `42P18: Indeterminate datatype` and `btrim(integer)` errors.
    - MariaDB and MySQL leverage regex replacement (`REGEXP_REPLACE`) for exact 4-character
      whitespace handling in `IS_BLANK`, `IS_PRESENT`, and `???`.
    - SQLite utilizes multi-character trim (`' ' || char(9) || char(13) || char(10)`).
    - Fully validated against pinned Docker instances of MariaDB 11.8, MySQL 8.4, PostgreSQL 17,
      and SQLite with 0 differences across 433 expressions and 22 row rules.
    - Container functions `GET` and `PATH` remain strictly in-memory (registered with refusal
      `takes a container, and a SQL expression is a scalar`).

conformance 720 · sql cases 462 · mutations 161

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
