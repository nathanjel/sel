#!/usr/bin/env bash
# The registry of SEL implementations.
#
# Every tool in this directory iterates this list rather than naming hosts, so
# adding a fifth implementation is one entry here plus the five entry points
# described in tools/README.md — no changes to check.sh, fuzz.sh or the rest.
#
# Override to narrow a run:   SEL_IMPLS="js cpp" tools/fuzz.sh

SEL_IMPLS="${SEL_IMPLS:-js js-bundle php cpp lisp}"

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
    *)    echo "unknown implementation: $impl" >&2; return 2 ;;
  esac
}

# Each implementation's own unit tests, covering the layers underneath the
# conformance suite. Optional: js and php have none, and say so by succeeding.
impl_unit() {
  local impl="$1"; shift
  case "$impl" in
    js|js-bundle|php) return 0 ;;
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
    js-bundle) [ -f dist/sel.mjs ] ;;
    php)  command -v php  >/dev/null 2>&1 ;;
    cpp)  [ -x cpp/build/conformance ] ;;
    lisp) command -v sbcl >/dev/null 2>&1 && [ -x lisp/bin/conformance ] ;;
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
