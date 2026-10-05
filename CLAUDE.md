# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SEL is a tiny expression language for validation rules, implemented independently in every host language — JS (`js/`), PHP (`php/`), Python (`python/`), C++23 (`cpp/`), Common Lisp (`lisp/`), Go (`go/`), Rust (`rust/`) — and held to one written spec by a shared conformance suite and a differential fuzzer. Cross-host byte-identical agreement *is* the product. Each host also carries a SEL→SQL translator (`*/sql/`, `cpp/sel_sql*`, `go/sel/sql/`, `rust/src/sql/`) and an in-memory relational pipeline (`.>` operator, `FILTER`/`MAP`/`LINK`/`BUCKET`/…).

`README.md` is the short pitch and `docs/README.md` the documentation map; `docs/contributing.md` is the contributor guide and **its "traps" section is required reading before touching any host** — every item there is a real divergence that was found here.

## The one rule

**Spec first, then conformance cases, then every host, then `tools/check.sh`.** Never implement in one host and "port it later"; a feature that exists in one host is a feature nobody has tested. No implementation is the reference — when hosts disagree, `spec/` and `conformance/` decide which is wrong. When the fuzzer finds a disagreement, add the minimal `.selt` case *before* fixing any host.

Corollary: **never use the host's own idea of anything the language defines** — no floats (use the hand-written decimal core), no native string length/case/compare (UTF-8 codec is hand-written; `UPPER`/`LOWER` are ASCII-only by decision), no native regex flags (`\d`/`\w`/`\s` are rewritten to ASCII classes; `^`/`$` lowered to `\A`/`\z` in Perl-flavoured engines), no `int()`/`DIGIT-CHAR-P`/`isdigit()` before an explicit ASCII check, no `str.splitlines()`/`strip()`/`round()` in Python.

## Commands

Everything runs from the repository root. `tools/impls.sh` is the host registry; every tool iterates it, and `SEL_IMPLS="js cpp" <tool>` narrows any run.

```
tools/check.sh                       everything; "ALL GREEN" or it isn't done
                                     refuses a partial roster — build every host first (below)
                                     starts pinned Docker databases for the DSN-backed layers and
                                     FAILS if it cannot; SEL_SKIP_DB_TESTS=1 opts out,
                                     SEL_SQL_<DIALECT>_DSN supplies your own servers; likewise
                                     the Python unit lane fails without pytest (SEL_SKIP_PYTHON_UNIT=1).
                                     Other opt-outs: SEL_SKIP_SANITIZERS=1 (C++ TSan/ASan),
                                     SEL_SKIP_SQL_BUDGETS=1. SEL_IMPLS narrows the roster and
                                     SEL_EXTRA_IMPLS adds configurations (e.g. python-wheel).
                                     A narrowed or opted-out run ends "GREEN, PARTIAL — … not run /
                                     opted out: …", never "ALL GREEN"
cd cpp && make                       build build/{conformance,batch,e2e,api,sel,unit,sqlt,...}
make -C go                           go/build/{conformance,sqlt,sel,hybrid-driver,...,example-<cat>};
                                     tools drop Go while any .go is newer than go/build
bash rust/build.sh                   rust/build/{conformance,sqlt,sel,...,example-<cat>}; tools see Rust
                                     only while rust/build/inputs.sha256 matches its sources, so an
                                     edit under rust/src, rust/dev (the unpublished harness crate:
                                     runners, examples, benchmarks) or examples/*/rust.rs needs a rebuild
npm run build                        dist/sel.mjs + dist/sel.min.mjs (js-bundle roster entries)
```

Conformance suite (`conformance/*.selt`), per host; pass file paths to run one file:

```
node js/bin/conformance.mjs [conformance/07-text.selt]
php  php/bin/conformance
cpp/build/conformance
lisp/bin/conformance
PYTHONPATH=$PWD/python python3 python/bin/conformance.py
rust/build/conformance
go/build/conformance
```

SQL translation cases (`sql/cases/*.sqlt`), per host; args are name substrings:

```
node js/bin/sqlt.mjs [agg.static]      php php/bin/sqlt    cpp/build/sqlt
lisp/bin/sqlt                          PYTHONPATH=$PWD/python python3 python/bin/sqlt
rust/build/sqlt                        go/build/sqlt
```

Unit tests (layers under the suite — decimal, utf8, value; JS and PHP have `tools/check-js-optimizer.mjs` / `tools/check-php-optimizer.php` instead, which also execute hybrid plans):

```
cd cpp && make test                  unit then conformance;  make asan  runs unit, conformance, the SQL suite
                                     and the SQL unit under the address, leak and UB sanitizers;
                                     make tsan  runs every thread-sanitizer race probe
lisp/bin/test                        FiveAM
PYTHONPATH=$PWD/python python3 -m pytest -q python/tests
cd go && go test -race ./...
cd rust && cargo test --workspace     (allocation budgets in rust/tests/*_allocations.rs are exact)
```

Other lanes worth running individually while iterating:

```
tools/fuzz.sh 4000 <seed>            differential fuzz (values, error codes AND positions)
tools/fuzz-sql.sh 2000 <seed>        same for the SQL translators
tools/check-docs.sh                  every `=>` example in docs runs on every host
tools/check-decimal.sh 20000         decimal cores vs Python's decimal module
tools/e2e.sh / tools/check-api.sh    host API parity
tools/mutate-sql.sh                  breaks the SQL layer ~160 ways; checks must notice
tools/check-sql-oracle.sh            translated SQL vs a real DB (skips without a DSN; the gate provides one)
tools/oracle-db.sh                   spins up throwaway MySQL/Postgres containers for the above
tools/check-examples.sh [cat…]       examples/<cat>/ in every host: byte-identical output, equal to output.txt
tools/check-usage.sh [cat…]          the LIVE (database) examples, every host, in tools/usage.Dockerfile's image
                                     against its own throwaway PostgreSQL/MariaDB (+ SQLite files)
tools/check-runners.sh, check-cli-source.sh, check-corpus-bytes.sh   the runner, CLI and corpus-reader contracts
tools/check-go-fragments.sh, check-rust-fragments.sh, check-ref-fragments.sh   examples/fn-* builtins in place
python3 tools/check-hybrid-parity-driver.py rust rust/build/hybrid-driver --application   hybrid plans vs SQLite
                                     (JS/Python twins, Go: check-hybrid-parity-go.py, Lisp: lisp/bin/hybrid-driver)
python3 tools/check-roster.py        no sentence that counts the hosts, no host list missing a host
node tools/build-docs.mjs [--check|--serve 8080]   the HTML site from the Markdown (site/); --check = links
docker build -f docs/Dockerfile -t sel-docs .      the site as an nginx image
```

REPLs: `node js/bin/sel.mjs`, `php php/bin/sel`, `cpp/build/sel`, `lisp/bin/sel`, `PYTHONPATH=$PWD/python python3 -m sel`, `rust/build/sel`, `go/build/sel` (`-e 'expr'` for one-shot, `--deps` for static dependencies).

## Generated, committed artifacts — regenerate, don't hand-edit

These are authored once and rendered by Node scripts (into every host's source language, or into an example seed); three more are written by Python generators (below the table). The renderings are **committed** (a consumer of any host must never need Node), and `tools/check.sh` fails if they are stale. Every generator has a strict command line: no argument writes, `--check` verifies without writing, `--help` prints usage and never writes, and an unknown argument is refused (`tools/check-generators.sh`).

| Authored source | Generator | Outputs |
|---|---|---|
| `sql/dialects/*.json` (format: `sql/MAP.md`) | `node tools/gen-sql-map.mjs` | `php/src/Sql/MapData.php`, `python/sel/sql/_map.py`, `js/src/sql/_map.mjs`, `cpp/sel_sql_map_data.cpp`, `lisp/src/sql/map-data.lisp`, `go/sel/sql/map_data_gen.go`, `rust/src/sql/map_data.rs`, plus each host's `*map_replay*` (`go/bin/sqlreplay/replay_data_gen.go`, `rust/dev/src/bin/map_replay_data.rs`) |
| `examples/lib/tickets-generate.sel` | `node tools/gen-usage-seed.mjs` | `examples/sql-complex/seed.postgresql.sql` — the same rows `examples/memory-complex` generates in memory |
| `sql/cases/*.sqlt` | `node tools/gen-sql-cases.mjs` | `php/bin/CaseData.php`, `python/bin/case_data.py`, `js/bin/case-data.mjs`, `cpp/bin/case_data.cpp`, `lisp/bin/case-data.lisp`, `go/bin/sqlt/case_data_gen.go`, `rust/dev/src/bin/sqlt/case_data.rs` |
| `spec/limits.json` (format: `spec/limits.md`) | `node tools/gen-limits.mjs` | `js/src/_limits.mjs`, `python/sel/_limits.py`, `php/src/Limits.php`, `cpp/sel_limits.hpp`, `lisp/src/limits.lisp`, `go/internal/limits/limits.go`, `rust/src/limits.rs`, `docs/reference/limits.md` — the generator refuses to render unless the spec text states every value and code; each host's `MAX_DEPTH`/decimal caps are defined from its rendering; `tools/check-error-codes.sh` requires every host to raise exactly the catalogued codes |
| `spec/math-ops.json` (format: `spec/math-ops.md`) | `node tools/gen-math-ops.mjs` | `js/src/_math_ops.mjs`, `python/sel/_math_ops.py`, `php/src/MathOps.php`, `cpp/sel_math_ops.hpp`, `lisp/src/math-ops.lisp`, `go/internal/mathops/math_ops.go`, `rust/src/math_ops.rs`, `docs/internals/math-ops.md` — each host's math-plan compiler classifies through its rendering; opcode numbers stay native |
| `spec/builtins.json` (format: `spec/builtins.md`; also the SQL-map generator's arity authority, and `tools/check-manifest.sh` probes every host's accepted counts and binding forms against it) | `node tools/gen-builtins.mjs` | `js/src/_builtin_manifest.mjs`, `python/sel/_builtin_manifest.py`, `php/src/BuiltinManifest.php`, `cpp/sel_builtin_manifest.hpp`, `lisp/src/builtin-manifest.lisp`, `go/internal/manifest/builtins.go`, `rust/src/manifest/builtins.rs`, `docs/reference/builtins.md` — each host's `define` checks itself against its rendering at startup |

The Python generators write shared test data: `python3 tools/gen-decimal-cases.py` (the decimal conformance cases, from Python's `decimal` as oracle), `python3 tools/gen-regex-ambiguity-cases.py` (`conformance/28b-regex-ambiguity.selt`, from `tools/regex-ambiguity-ref.py`) and `python3 tools/gen-sql-scope-cases.py` (`sql/cases/48-scope-and-slots.sqlt`, derived from alpha-equivalent controls).

Editing a dialect or a `.sqlt` case means running the generator afterwards. `tools/check-generated.sh` verifies both (`--check` diffs content).

## Architecture

### Same shape in every host

The hosts share one layering so they can be read side by side: errors → utf8 → decimal → value → registry → lexer → parser → evaluator (+ `Args`) → builtins → host API (`js/src/sel.mjs`, `php/src/Sel.php`, `cpp/sel.hpp`, `lisp/src/sel.lisp`, `python/sel/__init__.py`, `go/sel/program.go`, `rust/src/lib.rs`). Port work goes in that dependency order. JS, Python and Lisp are file for file alike; C++ is one TU (`cpp/sel.cpp`, `// --- section` comments instead of files), and PHP, Go and Rust group some layers differently (Rust keeps its errors in `utf8.rs`, its lexer in `parser.rs` and its function table in `builtins/mod.rs`; Go and Rust keep join planning top-level). The table in `docs/contributing.md` §"Where everything lives" is the actual map, concern by concern, for every host — keep it true when you move code.

Every parser is precedence climbing over the same productions (`parse_program → parse_sequence → parse_list → parse_term → parse_prefix → parse_postfix → parse_primary`, spelled in each language's case; Rust's are `sequence`/`list`/`term`/`prefix`/`postfix`/`primary`); `python/sel/parser.py`'s docstring is the rationale. Parse and eval nesting are both capped at 200 (`E_DEPTH`) — any new recursion must be counted.

### Key semantics that shape the code

- **No statements.** A program is one expression; `IF`, `COND` and the aggregates are ordinary registry entries declared `lazy: true` that receive argument *nodes* and evaluate what they choose. Adding a function has two lanes: strict (values via the `Args` accessors — `args.nonNegInt(0)` etc. — which do arity/type/position errors for you) and lazy (nodes + frames). Start strict; go lazy only when something must *not* be evaluated. Worked examples in every host: `examples/fn-simple/`, `examples/fn-complex/`.
- **Values alias; assignment copies.** `Value` is a handle everywhere (in C++ a `shared_ptr` handle — copying it is *not* a deep copy, use `clone()`). Deep copies happen at `,` (child and value) and the assignment store in every host; C++ also clones what `MAP` and `FILTER` collect (PHP what `FILTER` collects), which no program can observe because a binder cannot be assigned. There are no lazy values (`LAZY_RECORD` was removed for making that copy observable). Adding a copy elsewhere, or a builtin that stores a value inside another without cloning, is a divergence (and in C++ a leak — `make asan` checks).
- **Assignment targets resolve to a path**, and the store lands at that path in the tree *as it exists after the RHS ran* (spec §5.7). Index expressions still evaluate once, in order, before the RHS.
- **Strict left-to-right evaluation** — in C++ bind coerced operands to named locals first; argument evaluation order is unspecified there.
- **Unknown function names are a compile-time error**, so builtins must be registered before parsing: PHP has no autoloader (`php/src/bootstrap.php`), JS imports from `js/src/builtins/index.mjs`, Python from `python/sel/builtins/__init__.py`.
- **Errors carry a stable code and the innermost failing node's position.** Tests assert codes and positions, never message text.
- **Externally facing vocabulary must not look like SQL.** The grouping verb is `BUCKET` (never `GROUP_BY`); SQL-clause names (`groupBy`, `having`, `orderBy`) belong only to the translator's internal plan structs.

### SEL→SQL layer

Opt-in per host (PHP: `Sql/bootstrap.php`, which Composer loads on first use of a `Sel\Sql` class; JS: `sel-lang/sql` entry point, not in the bundle). Pipeline: `compile` → stage 1 normalise (inline assignments, refuse non-expression shapes) → stage 2 lower aggregates to unrolls / `EXISTS` / subqueries → stage 3 kind inference (`NUM|TEXT|BOOL|BIN|UNKNOWN`, folded into the render walk) → stage 4 render via the dialect map. Whole-expression refusal: nothing becomes characters until a `Fragment` is asked. Dialects inherit key-by-key along `ansi → mysql-family → {mariadb, mysql}`, `ansi → postgresql`, `ansi → sqlite`; `ansi` must say what *standard* SQL says (it is strongly typed — MySQL's silent coercions must not leak into it). `hybrid` splits a pipeline into a maximal SQL-pushdown prefix plus an in-memory suffix; `relational-plan` is the statement IR. Design: `docs/internals/sql-translation.md`; kinds: `docs/internals/sql-kinds.md`; errors: `sql/errors.md`. The *data* (map, cases) is shared; the *walk* is transcribed per host.

### Test-file formats

`.selt` (`conformance/README.md`) and `.sqlt` (`sql/cases/README.md`) are line-oriented `### name: category.thing.detail` / `--- section` / `===` records, so no host needs a JSON parser for the language suite. Names are unique suite-wide; add a `--- note` when a case encodes a decision. Corpus files for batch/fuzz use `### ` record markers and the rule "exactly one trailing newline removed" — readers that get this wrong produce phantom position disagreements.

## Docs layout and status

- `spec/` (SPEC.md, grammar.md, errors.md) and `conformance/` are normative. User docs: `docs/overview.md`, `parity.md`, `syntax.md`, `operators.md`, `functions.md`, `sql.md`, `extending.md`, `docs/usage/*.md` (host API, REPL, validation, scripting, SQL conditions/pipelines per schema), `docs/reference/` (generated). `docs/contributing.md` is for contributors; `docs/internals/` holds the SQL design docs (§-numbers are cited from code comments — do not renumber).
- Every `expr  => result` example in `README.md`, `docs/*.md` and `docs/usage/*.md` (in ```` ```sel ```` blocks and in table-cell code spans) is executed by every host (`tools/check-docs.sh`); `<!-- from: path#region -->` blocks must be byte-identical to that file's `EXAMPLE-BEGIN region`/`EXAMPLE-END region` (dedented; a file with no markers — `.sel`, `output.txt` — is quoted whole) (`tools/check-snippets.py`) — edit the example, not the doc. Per-host snippets are `<!-- tabs -->` groups of `<details>` (GitHub: collapsible; site: tabs). Every page is in `docs/nav.json`; `node tools/build-docs.mjs --check` fails on any broken link/anchor.
- `examples/<cat>/` holds one file per host plus `output.txt` (the transcript the docs quote, enforced by the lanes); a `LIVE` marker means it needs databases (`tools/check-usage.sh`, per-category databases `sel_<cat>` seeded from `seed.<dialect>.sql`); `examples/lib/` (`LIBRARY`) holds the per-host DB runner `db.*` and the support-desk SEL programs. Host functions (`register_function` & co., SPEC §8.1) are the public extension API; an application may give one a per-dialect SQL spelling (`sql/MAP.md` §4.7: registered first, arity recorded, `args` kinds incl. `LIST`, caveat `host-function`); `.sqlt` `--- register` has a `{"function": [name, min, max]}` op for it.
- Packages ship only the user docs: `package.json` `files`, the `pyproject.toml` sdist `include` and `.gitattributes` `export-ignore` each allowlist them file by file, and `tools/check-package-docs.sh` holds the three to one list. A new user-facing doc goes in all four places; anything else in `docs/` stays out of releases.
- Issue tracking, review findings, worklists and measurement logs are not kept in the tree (they were removed in the documentation overhaul and live in git history); tools that produce results write them to their own ignored `tools/<tool>/results/`.
- `CHANGELOG.md`'s top heading is one of the version sources `tools/check-version.sh` holds equal (it also holds the manifests' one package description equal); `composer.json` deliberately has no `version` field. Release steps are in `PACKAGING.md` §"Before any release". Never re-tag a pushed tag — bump instead.
