#!/usr/bin/env bash
# The runner contract (tools/README.md, "What an implementation must provide"),
# in every host: a runner given a path it cannot read -- missing, or a directory
# -- exits non-zero with `cannot read <path>` and no stack trace, and a run that
# executed ZERO cases (an empty file, a filter that matched nothing) exits
# non-zero instead of reporting "0 passed" as a success.
#
#   tools/check-runners.sh
#
# Roles: conformance and batch (missing path, directory, empty file) and sql
# (a filter that matches nothing). The wording of the refusal is the host's own
# beyond `cannot read <path>`; the contract is the exit status and that it is a
# controlled refusal. A mistyped path that "passes" is how a gate goes green
# having run nothing.
set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/a-directory"
: > "$WORK/empty.selt"
: > "$WORK/empty.selc"
MISSING="$WORK/no-such-dir/no-such-file"
CRASH_MARK='(Traceback|Unhandled|node:internal|at .*\.m?js:[0-9]|Fatal error|Stack trace|panic:|goroutine |SB-|debugger invoked|Segmentation|core dumped|thrown in)'

IMPLS="$(available_impls)"

# expect <label> <role> <impl> <want: path|zero> [path] -- <args...>
expect() {
  local label="$1" role="$2" impl="$3" want="$4" path="$5"; shift 6
  local out="$WORK/$impl.$role.$label" rc why=""
  "impl_$role" "$impl" "$@" > "$out" 2>&1 < /dev/null
  rc=$?
  if [ "$rc" -eq 0 ]; then why="exit 0"
  elif [ "$rc" -ge 126 ]; then why="exit $rc (a signal or a missing runner)"
  elif grep -Eq "$CRASH_MARK" "$out"; then why="host crash text"
  elif [ "$want" = path ] && ! grep -qF "cannot read $path" "$out"; then why="no 'cannot read $path'"
  fi
  [ -z "$why" ] || printf 'FAIL %-12s %-14s %-24s %s: %s\n' "$role" "$impl" "$label" "$why" \
    "$(head -c 160 "$out" | tr '\n' ' ')"
}

run_impl() {
  local impl="$1"
  expect missing-path   conformance "$impl" path "$MISSING.selt"        -- "$MISSING.selt"
  expect directory      conformance "$impl" path "$WORK/a-directory"    -- "$WORK/a-directory"
  expect empty-file     conformance "$impl" zero ""                     -- "$WORK/empty.selt"
  expect missing-path   batch       "$impl" path "$MISSING.selc"        -- "$MISSING.selc"
  expect directory      batch       "$impl" path "$WORK/a-directory"    -- "$WORK/a-directory"
  expect empty-file     batch       "$impl" zero ""                     -- "$WORK/empty.selc"
  case "$impl" in
    js-bundle|js-bundle-min) ;;   # no SQL layer: impl_sql succeeds silently by contract
    *) expect filter-matches-nothing sql "$impl" zero "" -- zzz-no-case-has-this-name ;;
  esac
}

for impl in $IMPLS; do
  sel_slot run_impl "$impl" > "$WORK/report.$impl" 2>&1 &
done
wait
failures=0
for impl in $IMPLS; do
  cat "$WORK/report.$impl"
  failures=$((failures + $(grep -c '^FAIL' "$WORK/report.$impl")))
done
if [ "$failures" -gt 0 ]; then
  echo "$failures runner contract check(s) failed"
  exit 1
fi
echo "runner contract: bad paths and empty runs refused across: $IMPLS"
