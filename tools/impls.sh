#!/usr/bin/env bash
# The registry of SEL implementations.
#
# Every tool in this directory iterates this list rather than naming hosts, so
# adding a fifth implementation is one entry here plus the five entry points
# described in tools/README.md — no changes to check.sh, fuzz.sh or the rest.
#
# Override to narrow a run:   SEL_IMPLS="js cpp" tools/fuzz.sh
#
# python-wheel is a seventh host and is deliberately not in the default list: it
# runs the *built package* rather than the source tree, so it needs a build and
# an install before it means anything, and a run that silently skipped it would
# be worse than one that never offered it. Widen to include it:
#
#   SEL_IMPLS="js js-bundle php cpp lisp python python-wheel" tools/check.sh

SEL_IMPLS="${SEL_IMPLS:-js js-bundle php cpp lisp python}"

# Where python-wheel looks for its interpreter: a venv with the built wheel
# installed, so the *package* is held to the same suite as the source tree.
#   python3 -m build --outdir dist/python
#   python3 -m venv python/.venv-wheel
#   python/.venv-wheel/bin/pip install dist/python/*.whl
SEL_PY_WHEEL_BIN="${SEL_PY_WHEEL_BIN:-$PWD/python/.venv-wheel/bin/python3}"

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
    php)  php tools/run-batch.php "$@" ;;
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
impl_api() {
  local impl="$1"; shift
  case "$impl" in
    js)   node tools/api.mjs "$@" ;;
    js-bundle) SEL_JS_ENTRY="$PWD/dist/sel.mjs" node tools/api.mjs "$@" ;;
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
    js-bundle) echo "js-bundle: decimal core is js/src/decimal.mjs, covered above" ;;
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
    js|js-bundle|cpp|lisp) return 0 ;;          # no SQL layer yet
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
impl_oracle() {
  local impl="$1"; shift
  case "$impl" in
    php)  php php/bin/sqlo "$@" ;;
    js|js-bundle|cpp|lisp) return 0 ;;          # no SQL layer yet
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
    js|js-bundle|cpp|lisp) return 0 ;;
    python|python-wheel) return 0 ;;
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# Each implementation's own unit tests, covering the layers underneath the
# conformance suite. Optional: js and php have none, and say so by succeeding.
impl_unit() {
  local impl="$1"; shift
  case "$impl" in
    js|js-bundle|php) return 0 ;;
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
