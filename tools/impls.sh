#!/usr/bin/env bash
# The registry of SEL implementations.
#
# Every tool in this directory iterates this list rather than naming hosts, so
# adding a fifth implementation is one entry here plus the five entry points
# described in tools/README.md — no changes to check.sh, fuzz.sh or the rest.
#
# Override to narrow a run:   SEL_IMPLS="js cpp" tools/fuzz.sh
#
# python-wheel is an eighth configuration and is deliberately not in the default
# list: it
# runs the *built package* rather than the source tree, so it needs a build and
# an install before it means anything, and a run that silently skipped it would
# be worse than one that never offered it. Widen to include it:
#
#   SEL_IMPLS="js js-bundle php cpp lisp python python-wheel" tools/check.sh
#
# js-bundle-min IS in the default list, unlike python-wheel, because it needs no
# install: `npm run build` writes dist/sel.mjs and dist/sel.min.mjs in one step,
# so a tree that can run js-bundle can already run this. It is published as
# package.json's "./bundle.min" and was the one shipped artefact nothing graded --
# a minifier that renamed something it should not have would have reached a user
# before it reached the suite.

SEL_IMPLS="${SEL_IMPLS:-js js-bundle js-bundle-min php cpp lisp python}"

# Where python-wheel looks for its interpreter: a venv with the built wheel
# installed, so the *package* is held to the same suite as the source tree.
#   python3 -m build --outdir dist/python
#   python3 -m venv python/.venv-wheel
#   python/.venv-wheel/bin/pip install dist/python/*.whl
SEL_PY_WHEEL_BIN="${SEL_PY_WHEEL_BIN:-$PWD/python/.venv-wheel/bin/python3}"

# Extra flags for every `php` invocation below. Empty normally: a host is graded
# as it ships. tools/stress.sh sets `-d memory_limit=-1`, because it feeds every
# host a program of several hundred thousand nodes and PHP's default 128M ceiling
# stops it at about 90,000 -- which would report "PHP disagrees" for a question
# about how much memory PHP was configured to have rather than about the code.
SEL_PHP_FLAGS="${SEL_PHP_FLAGS:-}"

# The first implementation in the list is the reference the others are diffed
# against in fuzz.sh. It is only a reporting convenience: a disagreement is a
# disagreement whichever side of it you stand on, and spec/ decides who is wrong.

# --- entry points -----------------------------------------------------------
#
# Each takes the implementation name as $1 and the role's arguments after it.
# Each runs from the repository root.

impl_conformance() {
  local impl="$1"; shift
  case "$impl" in
    js)   node js/bin/conformance.mjs "$@" ;;
    js-bundle) SEL_JS_ENTRY="$PWD/dist/sel.mjs" node js/bin/conformance.mjs "$@" ;;
    js-bundle-min) SEL_JS_ENTRY="$PWD/dist/sel.min.mjs" node js/bin/conformance.mjs "$@" ;;
    php)  php php/bin/conformance "$@" ;;
    cpp)  cpp/build/conformance "$@" ;;
    lisp) lisp/bin/conformance "$@" ;;
    python) PYTHONPATH="$PWD/python" python3 python/bin/conformance.py "$@" ;;
    python-wheel) "$SEL_PY_WHEEL_BIN" python/bin/conformance.py "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

impl_batch() {
  local impl="$1"; shift
  case "$impl" in
    js)   node tools/run-batch.mjs "$@" ;;
    js-bundle) SEL_JS_ENTRY="$PWD/dist/sel.mjs" node tools/run-batch.mjs "$@" ;;
    js-bundle-min) SEL_JS_ENTRY="$PWD/dist/sel.min.mjs" node tools/run-batch.mjs "$@" ;;
    # Unquoted on purpose: SEL_PHP_FLAGS is a controlled internal variable
    # and its words are separate arguments.
    # shellcheck disable=SC2086
    php)  php $SEL_PHP_FLAGS tools/run-batch.php "$@" ;;
    cpp)  cpp/build/batch "$@" ;;
    lisp) lisp/bin/batch "$@" ;;
    python) PYTHONPATH="$PWD/python" python3 python/bin/batch.py "$@" ;;
    python-wheel) "$SEL_PY_WHEEL_BIN" python/bin/batch.py "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

impl_e2e() {
  local impl="$1"; shift
  case "$impl" in
    js)   node examples/e2e.mjs "$@" ;;
    js-bundle) SEL_JS_ENTRY="$PWD/dist/sel.mjs" node examples/e2e.mjs "$@" ;;
    js-bundle-min) SEL_JS_ENTRY="$PWD/dist/sel.min.mjs" node examples/e2e.mjs "$@" ;;
    php)  php examples/e2e.php "$@" ;;
    cpp)  cpp/build/e2e "$@" ;;
    lisp) lisp/bin/e2e "$@" ;;
    python) PYTHONPATH="$PWD/python" python3 examples/e2e.py "$@" ;;
    python-wheel) "$SEL_PY_WHEEL_BIN" examples/e2e.py "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# The host API surface: same probes, each through its own binding. See
# tools/check-api.sh.
# `--deps` on one source file, for tools/stress.sh. Every CLI prints one name per
# line and they already agree byte for byte, so this needs no normalising layer
# the way impl_batch does -- it is here so the roster lives in one file.
impl_deps() {
  local impl="$1"; shift
  case "$impl" in
    js)   node js/bin/sel.mjs --deps "$@" ;;
    js-bundle) SEL_JS_ENTRY="$PWD/dist/sel.mjs" node js/bin/sel.mjs --deps "$@" ;;
    js-bundle-min) SEL_JS_ENTRY="$PWD/dist/sel.min.mjs" node js/bin/sel.mjs --deps "$@" ;;
    # shellcheck disable=SC2086
    php)  php $SEL_PHP_FLAGS php/bin/sel --deps "$@" ;;
    cpp)  cpp/build/sel --deps "$@" ;;
    lisp) lisp/bin/sel --deps "$@" ;;
    python) PYTHONPATH="$PWD/python" python3 -m sel --deps "$@" ;;
    python-wheel) "$SEL_PY_WHEEL_BIN" -m sel --deps "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

impl_api() {
  local impl="$1"; shift
  case "$impl" in
    js)   node tools/api.mjs "$@" ;;
    js-bundle) SEL_JS_ENTRY="$PWD/dist/sel.mjs" node tools/api.mjs "$@" ;;
    js-bundle-min) SEL_JS_ENTRY="$PWD/dist/sel.min.mjs" node tools/api.mjs "$@" ;;
    php)  php tools/api.php "$@" ;;
    cpp)  cpp/build/api "$@" ;;
    lisp) lisp/bin/api "$@" ;;
    python) PYTHONPATH="$PWD/python" python3 python/bin/api.py "$@" ;;
    python-wheel) "$SEL_PY_WHEEL_BIN" python/bin/api.py "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

impl_decimal() {
  local impl="$1"; shift
  case "$impl" in
    js)   node tools/check-decimal.mjs "$@" ;;
    # The oracle is a whitebox check on js/src/decimal.mjs, which the bundle
    # inlines verbatim. Running it twice would test the same code.
    js-bundle|js-bundle-min) echo "$impl: decimal core is js/src/decimal.mjs, covered above" ;;
    php)  php tools/check-decimal.php "$@" ;;
    cpp)  cpp/build/check-decimal "$@" ;;
    lisp) lisp/bin/check-decimal "$@" ;;
    python) PYTHONPATH="$PWD/python" python3 python/bin/check-decimal.py "$@" ;;
    # The oracle is a whitebox check on python/sel/decimal.py, which the wheel
    # ships verbatim. Running it twice would test the same code.
    python-wheel) echo "python-wheel: decimal core is python/sel/decimal.py, covered above" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# The SQL translation cases in sql/cases/. Optional: an implementation with no
# SQL layer says so by succeeding, the same way impl_unit does. The cases assert
# an exact string, so running them under two hosts is what makes "the same SQL
# everywhere" a measurement rather than an intention.
impl_sql() {
  local impl="$1"; shift
  case "$impl" in
    php)  php php/bin/sqlt "$@" ;;
    js)   node js/bin/sqlt.mjs "$@" ;;
    # The bundle is built from js/src/sel.mjs, which does not import the SQL
    # layer -- it is a separate entry point (package.json "./sql"), so a host
    # that only wants the evaluator does not carry the translator. Nothing to
    # grade here, rather than something skipped.
    js-bundle|js-bundle-min) return 0 ;;
    cpp|lisp) return 0 ;;                       # no SQL layer yet
    python) PYTHONPATH="$PWD/python" python3 python/bin/sqlt "$@" ;;
    # The runner adds python/ to sys.path only when `sel` is not already
    # importable, so this grades the installed wheel and not the source tree.
    python-wheel) "$SEL_PY_WHEEL_BIN" python/bin/sqlt "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# The SQL semantic oracle in sql/oracle/: the same expressions evaluated by SEL
# and by a real database. Where impl_sql asks "is this the string we meant to
# emit?", this asks "does that string mean what SEL means?" -- the question a
# case file cannot answer about itself. Skips itself when no DSN is set.
# Translate a corpus and print one canonical line per program, so that the hosts
# WITH a translator can be diffed against each other. This is the lane
# docs/SQL-TRANSLATION.md §14 M7 asked for: impl_oracle answers "does the emitted
# SQL mean what SEL means?" and needs a database, this one answers "do the hosts
# emit the same thing?" and needs nothing -- which matters, because without it
# the SQL fuzz step did nothing at all on a machine with no DSN, and that is
# every machine by default.
impl_sqlfuzz() {
  local impl="$1"; shift
  case "$impl" in
    php)  php $SEL_PHP_FLAGS php/bin/sqlfuzz "$@" ;;
    js)   node js/bin/sqlfuzz.mjs "$@" ;;
    # The bundle does not import the SQL layer; see impl_sql.
    js-bundle|js-bundle-min) return 0 ;;
    cpp|lisp) return 0 ;;                       # no SQL layer yet
    python) PYTHONPATH="$PWD/python" python3 python/bin/sqlfuzz "$@" ;;
    python-wheel) "$SEL_PY_WHEEL_BIN" python/bin/sqlfuzz "$@" ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

impl_oracle() {
  local impl="$1"; shift
  case "$impl" in
    php)  php php/bin/sqlo "$@" ;;
    # js has a translator and still no oracle, for the reason M7 records: the
    # oracle measures whether the MAP means what SEL means, and the map is data
    # every host consumes unchanged, so a second harness would ask one server
    # the same question twice.
    js|js-bundle|js-bundle-min|cpp|lisp) return 0 ;;
    python|python-wheel) return 0 ;;            # M6
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# The design document's worked examples, checked against the cases they quote.
# Optional the same way impl_sql is.
impl_sqldoc() {
  local impl="$1"; shift
  case "$impl" in
    php)  php php/bin/sqldoc "$@" ;;
    js|js-bundle|js-bundle-min|cpp|lisp) return 0 ;;   # a property of the design doc
    python|python-wheel) return 0 ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# Each implementation's own unit tests, covering the layers underneath the
# conformance suite. Optional: js and php have none, and say so by succeeding.
impl_unit() {
  local impl="$1"; shift
  case "$impl" in
    js|js-bundle|js-bundle-min|php) return 0 ;;
    # `python3 -m pytest`, not `pytest`: the binary is often only on a venv's
    # PATH while the module is importable by the interpreter we actually use.
    python)
      python3 -c 'import pytest' 2>/dev/null || {
        echo "python: pytest not installed, unit tests skipped"; return 0; }
      PYTHONPATH="$PWD/python" python3 -m pytest -q python/tests ;;
    # Deliberately not given PYTHONPATH: these run against the installed package.
    python-wheel)
      "$SEL_PY_WHEEL_BIN" -c 'import pytest' 2>/dev/null || {
        echo "python-wheel: pytest not installed in the venv, unit tests skipped"; return 0; }
      "$SEL_PY_WHEEL_BIN" -m pytest -q python/tests ;;
    cpp)    [ -x cpp/build/unit ] && cpp/build/unit ;;
    lisp)   lisp/bin/test ;;
    *)      echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# True when the implementation can actually be run right now. C++ needs building
# first; a missing binary is reported as a skip rather than a failure, so a fresh
# clone can run the JS and PHP layers without a toolchain.
impl_available() {
  case "$1" in
    js)   command -v node >/dev/null 2>&1 ;;
    # Present *and* newer than every source file it is built from. A stale
    # bundle is a different implementation from the one in js/src, and it should
    # say so rather than fail later with a confusing TypeError.
    js-bundle) [ -f dist/sel.mjs ] && [ -z "$(find js/src -newer dist/sel.mjs -print -quit 2>/dev/null)" ] ;;
    js-bundle-min) [ -f dist/sel.min.mjs ] \
      && [ -z "$(find js/src -newer dist/sel.min.mjs -print -quit 2>/dev/null)" ] ;;
    php)  command -v php  >/dev/null 2>&1 ;;
    cpp)  [ -x cpp/build/conformance ] ;;
    lisp) command -v sbcl >/dev/null 2>&1 && [ -x lisp/bin/conformance ] ;;
    python) command -v python3 >/dev/null 2>&1 ;;
    # Present *and* newer than every source file it was built from, the same
    # guard js-bundle has. A stale wheel is a different implementation from the
    # one in python/sel, and it should say so rather than fail confusingly later.
    #
    # The comparison is against the *installed package*, not against the venv's
    # interpreter: the interpreter is a symlink to system Python whose mtime a
    # reinstall never touches, so guarding on it marked the wheel stale forever
    # after the first source edit — the guard could go off but never reset.
    # js-bundle compares against dist/sel.mjs, which is the artefact its build
    # rewrites; this is the same idea, spelled for pip.
    python-wheel)
      installed="$(echo python/.venv-wheel/lib/python*/site-packages/sel/__init__.py)"
      # -name '*.py': python/sel also holds __pycache__, whose mtime moves every
      # time the *source* implementation runs. Comparing against the directory
      # tree meant that running `python` marked `python-wheel` stale, so the two
      # could never both be available in one check.sh run.
      [ -n "${SEL_PY_WHEEL_BIN:-}" ] && [ -x "$SEL_PY_WHEEL_BIN" ] && [ -f "$installed" ] \
        && [ -z "$(find python/sel -name '*.py' -newer "$installed" -print -quit 2>/dev/null)" ] ;;
    *)    return 1 ;;
  esac
}

# The subset of SEL_IMPLS that is runnable. Quiet, because every tool calls it
# and a fresh clone with no C++ toolchain would otherwise repeat the same warning
# five times; check.sh reports the roster once at the top instead.
available_impls() {
  local out=""
  for impl in $SEL_IMPLS; do
    impl_available "$impl" && out="$out $impl"
  done
  echo "${out# }"
}

# What is being skipped, and why, for the one place that should say so.
missing_impls() {
  local out=""
  for impl in $SEL_IMPLS; do
    impl_available "$impl" || out="$out $impl"
  done
  echo "${out# }"
}
