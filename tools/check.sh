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

echo "implementations: $IMPLS"
echo "concurrency: $SEL_JOBS leaf commands, $SEL_PHP_JOBS of them php"

# An incomplete roster must never reach "ALL GREEN". Every differential layer
# degrades quietly to a no-op when there is nothing to compare against — e2e
# skips its only iteration, the fuzzer finds every one-element list unanimous —
# so a run with most of the hosts missing would otherwise pass having compared
# nothing with nothing.
if [ -n "$MISSING" ]; then
  echo "MISSING: $MISSING — not built, or the runtime is missing"
  echo "         build them (cd cpp && make; npm run build) or set SEL_IMPLS to say so"
  status=1
fi

# The Lisp runners compile the system into ASDF's cache on first use after a
# source edit. Several of them starting at once would each compile the same
# files, so the first Lisp step runs alone and warms the cache; the rest then
# load what it wrote. This is the one ordering constraint in the file.
case " $IMPLS " in *" lisp "*)
  echo "started  lisp warm-up"
  t0="$(date +%s)"
  # The SQL case runner loads the language and the SQL system; with a filter
  # nothing matches it runs no case, so this is the compile alone.
  lisp/bin/sqlt warm-up-no-such-case > "$LOGS/warmup.log" 2>&1 \
    || { status=1; cat "$LOGS/warmup.log"; }
  echo "done     lisp warm-up ($(( $(date +%s) - t0 ))s)"
  ;;
esac

for impl in $IMPLS; do
  step "conformance ($impl)" sel_slot impl_conformance "$impl"
done

for impl in $IMPLS; do
  case "$impl" in
    js|js-bundle|php) continue ;;   # their host-local checks run below, after the SQL cases
  esac
  step "unit tests ($impl)" sel_slot impl_unit "$impl"
done

# Unlike the two content checks after it, this one does not skip when Node is
# missing: a release ships these artifacts already generated so that no
# downstream user needs Node, and the machine cutting the release is exactly
# where a stale one would go unnoticed.
step "generated artifacts" sel_slot ./tools/check-generated.sh
step "sql dialect map" sel_slot ./tools/check-sql-map.sh
step "sql case data" sel_slot ./tools/check-sql-cases.sh

for impl in $IMPLS; do
  step "sql translation ($impl)" sel_slot impl_sql "$impl"
done

# Two independent checks, not a `case`: a case takes its first matching arm,
# and with js on the roster the PHP check never ran (review 2026-09-15).
case " $IMPLS " in *" js "*) step "JS optimizer" sel_slot node tools/check-js-optimizer.mjs ;; esac
case " $IMPLS " in *" php "*) step "PHP optimizer" sel_slot sel_php tools/check-php-optimizer.php ;; esac

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
step "sql mutations" ./tools/mutate-sql.sh
step "sql semantic oracle" ./tools/check-sql-oracle.sh
step "manifest versions" sel_slot ./tools/check-version.sh
step "host API parity" ./tools/check-api.sh
step "documentation examples" ./tools/check-docs.sh
step "worked examples, every host" ./tools/check-examples.sh
step "documentation quotes" sel_slot ./tools/check-snippets.py
step "decimal vs python oracle" ./tools/check-decimal.sh "${DECIMAL_COUNT:-4000}"
step "end to end, every host API" ./tools/e2e.sh
step "differential fuzz" ./tools/fuzz.sh "${FUZZ_COUNT:-4000}" "${FUZZ_SEED:-20260813}"
step "differential fuzz, sql" ./tools/fuzz-sql.sh "${SQL_FUZZ_COUNT:-2000}" "${SQL_FUZZ_SEED:-20260905}"

report

echo
echo "wall time: $(( $(date +%s) - started ))s"
if [ "$status" -eq 0 ]; then
  echo "ALL GREEN — $IMPLS"
else
  echo "FAILURES ABOVE"
fi
exit "$status"
