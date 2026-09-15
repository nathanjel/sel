# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

SEL is a tiny expression language for validation rules, implemented **five times** — JS (`js/`), PHP (`php/`), Python (`python/`), C++23 (`cpp/`), Common Lisp (`lisp/`) — and held to one written spec by a shared conformance suite and a differential fuzzer. Cross-host byte-identical agreement *is* the product. Each host also carries a SEL→SQL translator (`*/sql/`, `cpp/sel_sql*`) and an in-memory relational pipeline (`.>` operator, `FILTER`/`MAP`/`LINK`/`BUCKET`/…).

`README.md` is the tour; `docs/EXTENDING.md` is the contributor guide and **its "traps" section is required reading before touching any host** — every item there is a real divergence that was found here.

## The one rule

**Spec first, then conformance cases, then every host, then `tools/check.sh`.** Never implement in one host and "port it later"; a feature that exists in one host is a feature nobody has tested. No implementation is the reference — when hosts disagree, `spec/` and `conformance/` decide which is wrong. When the fuzzer finds a disagreement, add the minimal `.selt` case *before* fixing any host.

Corollary: **never use the host's own idea of anything the language defines** — no floats (use the hand-written decimal core), no native string length/case/compare (UTF-8 codec is hand-written; `UPPER`/`LOWER` are ASCII-only by decision), no native regex flags (`\d`/`\w`/`\s` are rewritten to ASCII classes; `^`/`$` lowered to `\A`/`\z` in Perl-flavoured engines), no `int()`/`DIGIT-CHAR-P`/`isdigit()` before an explicit ASCII check, no `str.splitlines()`/`strip()`/`round()` in Python.

## Commands

Everything runs from the repository root. `tools/impls.sh` is the host registry; every tool iterates it, and `SEL_IMPLS="js cpp" <tool>` narrows any run.

```
tools/check.sh                       everything (~11 layers); "ALL GREEN" or it isn't done
                                     refuses a partial roster — build C++ and the JS bundle first
cd cpp && make                       build build/{conformance,batch,e2e,api,sel,unit,sqlt,...}
npm run build                        dist/sel.mjs + dist/sel.min.mjs (js-bundle roster entries)
```

Conformance suite (`conformance/*.selt`), per host; pass file paths to run one file:

```
node js/bin/conformance.mjs [conformance/07-text.selt]
php  php/bin/conformance
cpp/build/conformance
lisp/bin/conformance
PYTHONPATH=$PWD/python python3 python/bin/conformance.py
```

SQL translation cases (`sql/cases/*.sqlt`), per host; args are name substrings:

```
node js/bin/sqlt.mjs [agg.link]        php php/bin/sqlt    cpp/build/sqlt
lisp/bin/sqlt                          PYTHONPATH=$PWD/python python3 python/bin/sqlt
```

Unit tests (layers under the suite — decimal, utf8, value; JS and PHP have `tools/check-js-optimizer.mjs` / `tools/check-php-optimizer.php` instead, which also execute hybrid plans):

```
cd cpp && make test                  unit then conformance;  make asan  for sanitizers
lisp/bin/test                        FiveAM
PYTHONPATH=$PWD/python python3 -m pytest -q python/tests
```

Other lanes worth running individually while iterating:

```
tools/fuzz.sh 4000 <seed>            differential fuzz (values, error codes AND positions)
tools/fuzz-sql.sh 2000 <seed>        same for the SQL translators
tools/check-docs.sh                  every `=>` example in docs runs on every host
tools/check-decimal.sh 20000         decimal cores vs Python's decimal module
tools/e2e.sh / tools/check-api.sh    host API parity
tools/mutate-sql.sh                  breaks the SQL layer ~160 ways; checks must notice
tools/check-sql-oracle.sh            translated SQL vs a real DB (skips without a DSN)
tools/oracle-db.sh                   spins up throwaway MySQL/Postgres containers for the above
```

REPLs: `node js/bin/sel.mjs`, `php php/bin/sel`, `cpp/build/sel`, `lisp/bin/sel`, `PYTHONPATH=$PWD/python python3 -m sel` (`-e 'expr'` for one-shot, `--deps` for static dependencies).

## Generated, committed artifacts — regenerate, don't hand-edit

Two things are authored once and rendered into every host's source language by Node scripts. The renderings are **committed** (a PHP/Python/C++/Lisp consumer must never need Node), and `tools/check.sh` fails if they are stale.

| Authored source | Generator | Outputs |
|---|---|---|
| `sql/dialects/*.json` (format: `sql/MAP.md`) | `node tools/gen-sql-map.mjs` | `php/src/Sql/MapData.php`, `python/sel/sql/_map.py`, `js/src/sql/_map.mjs`, `cpp/sel_sql_map_data.cpp`, `lisp/src/sql/map-data.lisp`, plus each host's `*map_replay*` |
| `sql/cases/*.sqlt` | `node tools/gen-sql-cases.mjs` | `php/bin/CaseData.php`, `python/bin/case_data.py`, `js/bin/case-data.mjs`, `cpp/bin/case_data.cpp`, `lisp/bin/case-data.lisp` |

Editing a dialect or a `.sqlt` case means running the generator afterwards. `tools/check-generated.sh` verifies both (`--check` diffs content).

## Architecture

### Same shape in every host

The five hosts are structured file-for-file alike so they can be read side by side (C++ is one TU, `cpp/sel.cpp`, with `// --- section` comments instead of files): errors → utf8 → decimal → value → registry → lexer → parser → evaluator (+ `Args`) → builtins → host API (`js/src/sel.mjs`, `php/src/Sel.php`, `cpp/sel.hpp`, `lisp/src/sel.lisp`, `python/sel/__init__.py`). Port work goes in that dependency order. The table in `docs/EXTENDING.md` §"Where everything lives" maps each concern to its file per host.

All five parsers are precedence climbing with the same function names (`parse_program → parse_sequence → parse_list → parse_term → parse_prefix → parse_postfix → parse_primary`); `python/sel/parser.py`'s docstring is the rationale. Parse and eval nesting are both capped at 200 (`E_DEPTH`) — any new recursion must be counted.

### Key semantics that shape the code

- **No statements.** A program is one expression; `IF`, `COND` and the aggregates are ordinary registry entries declared `lazy: true` that receive argument *nodes* and evaluate what they choose. Adding a function has two lanes: strict (values via the `Args` accessors — `args.nonNegInt(0)` etc. — which do arity/type/position errors for you) and lazy (nodes + frames). Start strict; go lazy only when something must *not* be evaluated. Worked examples in all five hosts: `examples/fn-simple/`, `examples/fn-complex/`.
- **Values alias; assignment copies.** `Value` is a handle everywhere (in C++ a `shared_ptr` handle — copying it is *not* a deep copy, use `clone()`). Deep copies happen at exactly five sites (`,` child, `,` value, assignment store, `MAP` result, `FILTER` element). Adding a copy elsewhere, or a builtin that stores a value inside another without cloning, is a divergence (and in C++ a leak — `make asan` checks).
- **Assignment targets resolve to a path**, and the store lands at that path in the tree *as it exists after the RHS ran* (spec §5.7). Index expressions still evaluate once, in order, before the RHS.
- **Strict left-to-right evaluation** — in C++ bind coerced operands to named locals first; argument evaluation order is unspecified there.
- **Unknown function names are a compile-time error**, so builtins must be registered before parsing: PHP has no autoloader (`php/src/bootstrap.php`), JS imports from `js/src/builtins/index.mjs`, Python from `python/sel/builtins/__init__.py`.
- **Errors carry a stable code and the innermost failing node's position.** Tests assert codes and positions, never message text.
- **Externally facing vocabulary must not look like SQL.** The grouping verb is `BUCKET` (never `GROUP_BY`); SQL-clause names (`groupBy`, `having`, `orderBy`) belong only to the translator's internal plan structs.

### SEL→SQL layer

Opt-in per host (PHP: separate `Sql/bootstrap.php` require; JS: `sel-lang/sql` entry point, not in the bundle). Pipeline: `compile` → stage 1 normalise (inline assignments, refuse non-expression shapes) → stage 2 lower aggregates to unrolls / `EXISTS` / subqueries → stage 3 kind inference (`NUM|TEXT|BOOL|BIN|UNKNOWN`, folded into the render walk) → stage 4 render via the dialect map. Whole-expression refusal: nothing becomes characters until a `Fragment` is asked. Dialects inherit key-by-key along `ansi → mysql-family → {mariadb, mysql}`, `ansi → postgresql`, `ansi → sqlite`; `ansi` must say what *standard* SQL says (it is strongly typed — MySQL's silent coercions must not leak into it). `hybrid` splits a pipeline into a maximal SQL-pushdown prefix plus an in-memory suffix; `relational-plan` is the statement IR. Design: `docs/SQL-TRANSLATION.md`; kinds: `docs/SQL-KINDS.md`; errors: `sql/errors.md`. The *data* (map, cases) is shared; the *walk* is transcribed per host.

### Test-file formats

`.selt` (`conformance/README.md`) and `.sqlt` (`sql/cases/README.md`) are line-oriented `### name: category.thing.detail` / `--- section` / `===` records, so no host needs a JSON parser for the language suite. Names are unique suite-wide; add a `--- note` when a case encodes a decision. Corpus files for batch/fuzz use `### ` record markers and the rule "exactly one trailing newline removed" — readers that get this wrong produce phantom position disagreements.

## Docs layout and status

- `spec/` (SPEC.md, grammar.md, errors.md) and `conformance/` are normative. `docs/LANGUAGE.md` is for rule authors; `docs/EXTENDING.md` for contributors.
- Every `=>` example in `README.md`/`docs/*.md` is executed by every host (`tools/check-docs.sh`); `<!-- from: path -->` blocks must be byte-identical to that file's `EXAMPLE-BEGIN`/`EXAMPLE-END` region (`tools/check-snippets.py`) — edit the example, not the doc. Editing a documented example means keeping it runnable.
- `docs/history/` — retired design docs, kept for the *why*; not maintained. `docs/interim/` — in-flight plans and roadmaps (porting worklist, optimizer plans, review remediation); treat as intent, not as a description of the tree.
- `CHANGELOG.md`'s top heading is one of eight version sources checked by `tools/check-version.sh`; `composer.json` deliberately has no `version` field. Release steps are in `PACKAGING.md` §"Before any release". Never re-tag a pushed tag — bump instead.
