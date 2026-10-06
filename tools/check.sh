#!/usr/bin/env bash
# Everything. Run this before believing anything.
#
# Every layer below is an independent read of the tree, so they all start at
# once and run side by side; the load is bounded by the two slot counts in
# tools/impls.sh (SEL_JOBS leaf commands, SEL_PHP_JOBS of them PHP), which the
# nested tools share, so the bound holds however many scripts are in flight.
# Each step writes to its own log and the logs are printed in the order the
# steps are listed here, once every step has finished, so the report reads the
# same as it did when the steps ran one after another; `started`/`done` lines
# come out as they happen, with the step's wall time.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

# --- databases ----------------------------------------------------------------
# Three layers ask a real server what the emitted SQL MEANS -- the semantic
# oracle, the seven live entries of the mutation catalogue, and the oracle half
# of the SQL fuzz lane -- and every one of them prints a skip and succeeds with
# no DSN. That made the whole database story invisible to a plain run for a
# long time (tools/oracle-db.sh's header says how). So the databases are not
# optional here: unless the caller provides servers or opts out, this script
# re-runs itself under `tools/oracle-db.sh run`, which starts the pinned Docker
# servers, exports their DSNs, and removes them when the run ends -- and if
# that cannot be done the run FAILS rather than passing having asked nobody.
#
#   SEL_SKIP_DB_TESTS=1         opt out; the three layers print their skips
#   SEL_SQL_<DIALECT>_DSN=...   use these servers; nothing is started
#   (neither)                   pinned Docker servers, or a failed run
if [ "${SEL_SKIP_DB_TESTS:-0}" = 1 ]; then
  echo "databases: skipped (SEL_SKIP_DB_TESTS=1); the DSN-backed layers print their skips"
elif [ -n "${SEL_CHECK_UNDER_ORACLE_DB:-}" ]; then
  echo "databases: pinned Docker servers, started by tools/oracle-db.sh for this run"
elif env | grep -q '^SEL_SQL_[A-Z]*_DSN=.'; then
  echo "databases: the SEL_SQL_*_DSN servers given; nothing started"
else
  echo "databases: starting pinned Docker servers (tools/oracle-db.sh run); SEL_SKIP_DB_TESTS=1 opts out, SEL_SQL_<DIALECT>_DSN supplies your own"
  SEL_CHECK_UNDER_ORACLE_DB=1 exec ./tools/oracle-db.sh run "$0" "$@"
fi

LOGS="$(mktemp -d)"
trap 'rm -rf "$LOGS"' EXIT
started="$(date +%s)"

status=0
names=()
pids=()
# step <name> <command...>: queue a step. It runs in the background into its
# own log; `report` prints the logs in queue order and folds the exit statuses
# into $status.
step() {
  local name="$1" idx="${#names[@]}"
  shift
  names[idx]="$name"
  (
    t0="$(date +%s)"
    "$@" > "$LOGS/$idx.log" 2>&1
    rc=$?
    echo "$rc" > "$LOGS/$idx.rc"
    echo "$(( $(date +%s) - t0 ))" > "$LOGS/$idx.time"
    if [ "$rc" -eq 0 ]; then echo "done     $name ($(cat "$LOGS/$idx.time")s)"
    else echo "FAILED   $name ($(cat "$LOGS/$idx.time")s, exit $rc)"; fi
  ) &
  pids[idx]=$!
  echo "started  $name"
}
report() {
  local idx rc
  for idx in "${!names[@]}"; do
    wait "${pids[idx]}"
    rc="$(cat "$LOGS/$idx.rc" 2>/dev/null || echo 1)"
    echo
    echo "=== ${names[idx]} === ($(cat "$LOGS/$idx.time" 2>/dev/null || echo '?')s)"
    cat "$LOGS/$idx.log"
    [ "$rc" -eq 0 ] || { status=1; echo "--- ${names[idx]}: exit $rc"; }
  done
}

IMPLS="$(available_impls)"
MISSING="$(missing_impls)"
# The configurations of the default roster this run does not include. SEL_IMPLS
# narrows a run on purpose, and that is a fine thing to do while iterating, but
# a narrowed run is not the gate: it says what it left out at the top and in its
# last line, so a documented partial roster cannot pass for the whole one.
NOT_RUN=""
for impl in $SEL_DEFAULT_IMPLS; do
  case " $SEL_IMPLS " in *" $impl "*) ;; *) NOT_RUN="$NOT_RUN $impl" ;; esac
done
NOT_RUN="${NOT_RUN# }"

# Lanes a caller may opt out of (slow ones, and ones that need a server or
# pytest), each named here and in the last line when it is skipped.
SKIPPED=""
[ "${SEL_SKIP_SANITIZERS:-0}" = 1 ] && SKIPPED="$SKIPPED SEL_SKIP_SANITIZERS"
[ "${SEL_SKIP_SQL_BUDGETS:-0}" = 1 ] && SKIPPED="$SKIPPED SEL_SKIP_SQL_BUDGETS"
# The database layers and the Python unit lane pass with a printed skip when
# opted out, so they are named here too: an opted-out run is not the gate.
[ "${SEL_SKIP_DB_TESTS:-0}" = 1 ] && SKIPPED="$SKIPPED SEL_SKIP_DB_TESTS"
[ "${SEL_SKIP_PYTHON_UNIT:-0}" = 1 ] && SKIPPED="$SKIPPED SEL_SKIP_PYTHON_UNIT"
SKIPPED="${SKIPPED# }"

echo "implementations: $IMPLS"
[ -z "$NOT_RUN" ] || echo "NOT RUNNING (SEL_IMPLS narrows the default roster): $NOT_RUN"
[ -z "$SKIPPED" ] || echo "opted out: $SKIPPED"
echo "concurrency: $SEL_JOBS leaf commands, $SEL_PHP_JOBS of them php"

# An incomplete roster must never reach "ALL GREEN". Every differential layer
# degrades quietly to a no-op when there is nothing to compare against — e2e
# skips its only iteration, the fuzzer finds every one-element list unanimous —
# so a run with most of the hosts missing would otherwise pass having compared
# nothing with nothing.
if [ -n "$MISSING" ]; then
  echo "MISSING: $MISSING — not built, or the runtime is missing"
  echo "         build them (make -C cpp; make -C go; bash rust/build.sh; npm run build)"
  echo "         or narrow SEL_IMPLS to say so"
  status=1
fi

# The Lisp runners compile the system into ASDF's cache on first use after a
# source edit. Several of them starting at once would each compile the same
# files, so the first Lisp step runs alone and warms the cache; the rest then
# load what it wrote. This is the one ordering constraint in the file.
case " $IMPLS " in *" lisp "*)
  echo "started  lisp warm-up"
  t0="$(date +%s)"
  # The SQL case runner loads the language and the SQL system; filtered to one
  # case, this is the compile and little else. (Not a filter that matches
  # nothing: the runner contract makes an empty run a failure.)
  lisp/bin/sqlt lex.number.canonical-form-survives > "$LOGS/warmup.log" 2>&1 \
    || { status=1; cat "$LOGS/warmup.log"; }
  echo "done     lisp warm-up ($(( $(date +%s) - t0 ))s)"
  ;;
esac

for impl in $IMPLS; do
  step "conformance ($impl)" sel_slot impl_conformance "$impl"
done

for impl in $IMPLS; do
  case "$impl" in
    js|js-bundle|js-bundle-min) continue ;;   # its host-local checks run below, after the SQL cases
  esac
  step "unit tests ($impl)" sel_slot impl_unit "$impl"
done

# Unlike the two content checks after it, this one does not skip when Node is
# missing: a release ships these artifacts already generated so that no
# downstream user needs Node, and the machine cutting the release is exactly
# where a stale one would go unnoticed.
step "generated artifacts" sel_slot ./tools/check-generated.sh
# ...and no generator writes on --help or an argument it does not know.
step "generator command lines" ./tools/check-generators.sh
step "error codes" sel_slot ./tools/check-error-codes.sh
step "manifest semantics" ./tools/check-manifest.sh
step "sql dialect map" sel_slot ./tools/check-sql-map.sh
step "sql case data" sel_slot ./tools/check-sql-cases.sh

for impl in $IMPLS; do
  step "sql translation ($impl)" sel_slot impl_sql "$impl"
done

# Two independent checks, not a `case`: a case takes its first matching arm,
# and with js on the roster the PHP check never ran.
case " $IMPLS " in *" js "*) step "JS optimizer" sel_slot node tools/check-js-optimizer.mjs ;; esac
case " $IMPLS " in *" php "*) step "PHP optimizer" sel_slot sel_php tools/check-php-optimizer.php ;; esac
case " $IMPLS " in *" js "*) step "JS SQL unit" sel_slot node tools/check-js-sql.mjs ;; esac
# The hybrid-parity corpus (sql/oracle/hybrid.json) against a real SQLite: the
# JS and Python twins run in process, Lisp, Go and Rust through a driver. PHP
# runs it as `sqlo hybrid` in the oracle lane. Every driver registers the
# corpus's application functions (POKE, HOSTF), so every run includes its
# `application` section (the twins always do). C++ has no SQLite binding in its
# standard library and no driver yet; its hybrid planner is held to the sqlt
# cases, sqlapi, and its own executed-plan unit tests (cpp/tests/sql_unit.cpp).
case " $IMPLS " in *" lisp "*) step "Lisp hybrid parity (SQLite)" sel_slot python3 tools/check-hybrid-parity-driver.py lisp lisp/bin/hybrid-driver --application ;; esac
case " $IMPLS " in *" go "*) step "Go hybrid parity (SQLite)" sel_slot python3 tools/check-hybrid-parity-go.py --application ;; esac
case " $IMPLS " in *" rust "*) step "Rust hybrid parity (SQLite)" sel_slot python3 tools/check-hybrid-parity-driver.py rust rust/build/hybrid-driver --application ;; esac
case " $IMPLS " in *" js "*) step "JS hybrid parity (SQLite)" sel_slot node tools/check-hybrid-parity.mjs ;; esac
case " $IMPLS " in *" python "*) step "Python hybrid parity (SQLite)" sel_slot env PYTHONPATH="$PWD/python" python3 tools/check-hybrid-parity.py ;; esac
case " $IMPLS " in *" js "*) step "JS plain vs optimised" sel_slot node tools/check-eval-equivalence.mjs ;; esac
case " $IMPLS " in *" php "*) step "PHP plain vs optimised" sel_slot sel_php tools/check-eval-equivalence.php ;; esac
case " $IMPLS " in *" python "*) step "Python plain vs optimised" sel_slot env PYTHONPATH="$PWD/python" python3 tools/check-eval-equivalence.py ;; esac
case " $IMPLS " in *" js "*) step "JS decimal guard" sel_slot node tools/check-js-decimal-guard.mjs ;; esac
case " $IMPLS " in *" js "*) step "JS runtime isolation and records" sel_slot node tools/check-js-runtime.mjs ;; esac
case " $IMPLS " in *" js-bundle "*) step "JS bundle runtime isolation" sel_slot node tools/check-js-runtime.mjs dist/sel.mjs ;; esac
case " $IMPLS " in *" js-bundle-min "*) step "JS minified runtime isolation" sel_slot node tools/check-js-runtime.mjs dist/sel.min.mjs ;; esac
case " $IMPLS " in *" php "*) step "PHP runtime" sel_slot sel_php tools/check-php-runtime.php ;; esac
case " $IMPLS " in *" php "*) step "PHP integration" sel_slot sel_php tools/check-php-integration.php ;; esac
case " $IMPLS " in *" php "*) step "PHP 8.1 (oldest supported)" sel_slot tools/check-php-version.sh ;; esac
# The four C++ race probes (a shared Program, the SQL layer, the regex cache, the
# host-function table) under ThreadSanitizer -- `make tsan` runs every one -- and the unit tests, the suite, the SQL cases and
# the SQL unit under AddressSanitizer + UBSan -- the clone-site and no-cycle
# invariants docs/contributing.md relies on. Each builds its own instrumented
# copy of the library, so they are the slowest C++ steps; SEL_SKIP_SANITIZERS=1
# opts out of both (and the last line says so).
if [ "${SEL_SKIP_SANITIZERS:-0}" != 1 ]; then
  case " $IMPLS " in *" cpp "*) step "C++ races (TSan)" sel_slot make -j4 -C cpp tsan ;; esac
  case " $IMPLS " in *" cpp "*) step "C++ sanitizers (ASan, UBSan)" sel_slot make -j4 -C cpp asan ;; esac
fi
case " $IMPLS " in *" js "*) step "JS metadata" sel_slot node tools/metadata/js.mjs ;; esac
# The JS lane's own checks of what it ships: the reference fragments through
# define() plus the host examples, and the .d.ts typings against the module.
# Guarded on the file so the gate runs on a tree that does not have them yet.
case " $IMPLS " in *" js "*) [ ! -f tools/check-js-examples.mjs ] || step "JS examples and reference fragments" sel_slot node tools/check-js-examples.mjs ;; esac
case " $IMPLS " in *" js "*) [ ! -f tools/check-js-dts.mjs ] || step "JS typings (sel.d.ts, sql.d.ts)" sel_slot node tools/check-js-dts.mjs ;; esac
case " $IMPLS " in *" php "*) step "PHP metadata" sel_slot sel_php tools/metadata/php.php ;; esac
case " $IMPLS " in *" python "*) step "Python metadata" sel_slot python3 tools/metadata/python.py ;; esac
case " $IMPLS " in *" lisp "*) step "Lisp metadata" sel_slot sbcl --script tools/metadata/lisp.lisp ;; esac
case " $IMPLS " in *" cpp "*) step "C++ metadata" sel_slot cpp/build/metadata ;; esac

# A more basic question than the cases: can this host's own API build the map
# it ships? If it cannot, the map is data rather than code, and a host with no
# JSON reader has nowhere to put it. sql/MAP.md §4.5¼.
#
# Every host's replay summary line is collected and they must all agree on the
# counts of registrations, lookups compared, and differences.
check_replay() {
  local impl out
  local -a rpids=()
  for impl in $IMPLS; do
    case "$impl" in js-bundle|js-bundle-min) continue ;; esac
    sel_slot impl_sqlreplay "$impl" > "$LOGS/replay.$impl" 2>&1 &
    rpids+=($!)
  done
  sel_wait "${rpids[@]}" || return 1
  local ref="" summary=""
  for impl in $IMPLS; do
    case "$impl" in js-bundle|js-bundle-min) continue ;; esac
    out="$(cat "$LOGS/replay.$impl")"
    echo "$impl: $out"
    if [ -z "$out" ]; then
      echo "$impl produced no sql map replay output" >&2
      return 1
    fi
    if [ -z "$ref" ]; then
      ref="$impl"; summary="$out"
    elif [ "$out" != "$summary" ]; then
      echo "sql map replay DISAGREEMENT between $ref and $impl:" >&2
      echo "  $ref: $summary" >&2
      echo "  $impl: $out" >&2
      return 1
    fi
  done
}
step "sql map replay" check_replay

step "sql documented examples" ./tools/check-sql-docs.sh
case " $IMPLS " in *" python "*) step "scale plans vs reference" sel_slot python3 tools/scale-test/run_benchmarks.py --plans-only ;; esac
# The three database layers share one schema and each drops and recreates its
# tables, so they run one at a time under a lock while everything else keeps
# going. The lock is held for the whole step: the fuzz lane's host-versus-host
# half and most of the mutation run need no server, and could release it
# sooner, but a finer grain would have to reach inside those scripts.
db_step() { local name="$1"; shift; step "$name" flock "$LOGS/db.lock" "$@"; }
# The lock is exported so the two long lanes can take it for exactly the checks
# that touch the shared schema instead of for their whole run. The mutation lane
# used to hold it for its entire duration (over an hour) and the SQL fuzz lane's
# host-against-host half, which needs no server, queued behind it: gate #2's wall
# time was that one lane plus the seconds the rest then took. Mutations run
# beside everything else now, at two thirds of the slot bound.
export SEL_DB_LOCK="$LOGS/db.lock"
export SEL_MUTATE_JOBS="${SEL_MUTATE_JOBS:-$(( (SEL_JOBS * 2 + 2) / 3 ))}"
step "sql mutations" ./tools/mutate-sql.sh
db_step "sql semantic oracle" ./tools/check-sql-oracle.sh
step "manifest versions and descriptions" sel_slot ./tools/check-version.sh
# No sentence counts the hosts and no documented host list leaves one out: the
# roster grew twice and left stale counts and lists behind both times.
step "host roster, every document" sel_slot python3 tools/check-roster.py
step "package contents: user docs only" sel_slot ./tools/check-package-docs.sh
# The C++ package as a consumer gets it: the files cpp/conanfile.py exports,
# built and installed, and cpp/test_package linked against the install.
step "C++ package, as installed" sel_slot ./tools/check-cpp-package.sh
# The Rust crate and the Go module as crates.io and the Go proxy deliver them:
# only their own directory, built and used from outside the repository.
case " $IMPLS " in *" rust "*) step "Rust package, as published" sel_slot ./tools/check-rust-package.sh ;; esac
case " $IMPLS " in *" go "*) step "Go module, as published" sel_slot ./tools/check-go-module.sh ;; esac
step "host API parity" ./tools/check-api.sh
step "host registry complete" ./tools/check-registry.sh
# What an operation may BUILD (SPEC §6.4) is refused cheaply, in a child process
# with a time and memory ceiling, in every host; likewise translator work and
# depth (docs/internals/sql-translation.md §7.4), and regex resource behaviour
# (the cases conformance/28-regex-portability.selt cannot hold safely).
# The three ceiling lanes hold a PHP slot for their whole run (sel_hold in
# tools/impls.sh), so a PHP probe never waits for a slot inside its ceiling.
step "output and work budgets" sel_hold php -- ./tools/check-budgets.sh
[ "${SEL_SKIP_SQL_BUDGETS:-0}" = 1 ] || step "SQL translator budgets" sel_hold php -- ./tools/check-sql-budgets.sh
step "regex resources" sel_slot sel_hold php -- python3 tools/check-regex-resources.py
# Every host's error messages follow one set of conventions (the codes and
# positions are the contract; the wording is held to the same rules). Guarded on
# the file so a tree that does not have the check yet still runs.
[ ! -f tools/check-messages.py ] || step "message conventions" python3 tools/check-messages.py
step "regex validator vs reference, every host" ./tools/check-regex-ambiguity-diff.sh
step "CLI source bytes and contract" ./tools/check-cli-source.sh
# Every runner refuses a path it cannot read and a run that executed nothing.
step "runner contract" ./tools/check-runners.sh
# Every batch runner reads a corpus as bytes and removes exactly one newline per
# record: CR and CRLF fixtures, and final records with and without a blank line.
step "corpus bytes, every batch runner" ./tools/check-corpus-bytes.sh
step "regex ambiguity reference" bash -c "python3 tools/regex-ambiguity-ref.py --self-check >/dev/null && python3 tools/regex-ambiguity-ref.py --cases conformance/28-regex-portability.selt >/dev/null && python3 tools/regex-ambiguity-ref.py --cases conformance/28b-regex-ambiguity.selt >/dev/null"
step "host SQL API parity" ./tools/check-sqlapi.sh
step "documentation examples" ./tools/check-docs.sh
step "worked examples, every host" ./tools/check-examples.sh
# The Go builtin fragments the reference examples quote, compiled into go/sel
# and run against their cases.
case " $IMPLS " in *" go "*) step "Go builtin fragments" sel_slot ./tools/check-go-fragments.sh ;; esac
# ...and the Rust ones, compiled into a copy of rust/ the same way.
case " $IMPLS " in *" rust "*) step "Rust builtin fragments" sel_slot ./tools/check-rust-fragments.sh ;; esac
# The same for the hosts whose fragments drop into a copy of the sources with no
# compiler: JS, Python, PHP and Lisp (C++: see the script's header).
step "reference builtin fragments" ./tools/check-ref-fragments.sh
# Clippy over the published crate and the harness crate, warnings denied. The
# lint set moves with the toolchain, so the lane runs only where clippy is
# installed and says so when it is not.
case " $IMPLS " in *" rust "*)
  if cargo clippy --version >/dev/null 2>&1; then
    step "Rust clippy" sel_slot sh -c 'cd rust && cargo clippy -q -p sel-lang --all-targets -- -D warnings && cargo clippy -q -p sel-lang-dev --all-targets -- -D warnings'
  else
    echo "note     Rust clippy: not installed (cargo clippy), lane not run"
  fi ;;
esac
# The six top-level host programs that ship in the packages run to exit 0.
step "top-level host examples" ./tools/check-host-examples.sh
step "documentation quotes" sel_slot ./tools/check-snippets.py
# The site's build without its output: every page in docs/nav.json renders, and
# every relative link and #anchor in the Markdown resolves -- on GitHub as much
# as in the site.
step "documentation links" sel_slot node tools/build-docs.mjs --check
# The SQL examples the documentation quotes, in every host against real
# PostgreSQL, MariaDB and SQLite, in tools/usage.Dockerfile's image (built on
# first use). Its own servers, so it needs no share of the db lock.
step "usage examples, real databases" ./tools/check-usage.sh
step "decimal vs python oracle" ./tools/check-decimal.sh "${DECIMAL_COUNT:-4000}"
step "end to end, every host API" ./tools/e2e.sh
step "differential fuzz" ./tools/fuzz.sh "${FUZZ_COUNT:-4000}" "${FUZZ_SEED:-20260813}"
# Joined rows over relations of mixed shapes, every host against a model of
# spec §7.4 written from the text (SEL-0053): conformance cases use rows of one
# shape, which is how most hosts came to build rows from their first element.
step "joined rows vs spec model" ./tools/join-rows-oracle/run.sh "${JOIN_ROWS_COUNT:-2000}" "${JOIN_ROWS_SEED:-530001}"
# A FILTER after a LINK against the same program with the join bound to a
# variable first (SEL-0054): the join tests conjuncts early at run time, and
# nothing that does may change a value, a key or an error.
step "join then filter as written" sh -c './tools/join-filter-oracle/run.sh "${JOIN_FILTER_COUNT:-1500}" "${JOIN_FILTER_SEED:-54001}" mixed && ./tools/join-filter-oracle/run.sh "${JOIN_FILTER_COUNT:-1500}" "$(( ${JOIN_FILTER_SEED:-54001} + 1 ))" uniform'
step "differential fuzz, sql" ./tools/fuzz-sql.sh "${SQL_FUZZ_COUNT:-2000}" "${SQL_FUZZ_SEED:-20260905}"

report

echo
echo "wall time: $(( $(date +%s) - started ))s"
if [ "$status" -eq 0 ] && [ -z "$NOT_RUN" ] && [ -z "$SKIPPED" ]; then
  echo "ALL GREEN — $IMPLS"
elif [ "$status" -eq 0 ]; then
  echo "GREEN, PARTIAL — $IMPLS${NOT_RUN:+; not run: $NOT_RUN}${SKIPPED:+; opted out: $SKIPPED}"
else
  echo "FAILURES ABOVE"
fi
exit "$status"
