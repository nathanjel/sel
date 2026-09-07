#!/usr/bin/env bash
# Everything. Run this before believing anything.

set -uo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

status=0
step() {
  echo
  echo "=== $1 ==="
  shift
  "$@" || status=1
}

IMPLS="$(available_impls)"
MISSING="$(missing_impls)"

echo "implementations: $IMPLS"

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

for impl in $IMPLS; do
  step "conformance ($impl)" impl_conformance "$impl"
done

for impl in $IMPLS; do
  case "$impl" in
    js|js-bundle|php) continue ;;   # no separate unit tests; the suite is the test
  esac
  step "unit tests ($impl)" impl_unit "$impl"
done

# Before the two content checks below, and unlike them it does not skip when
# Node is missing: a release ships these artifacts already generated so that no
# downstream user needs Node, and the machine cutting the release is exactly
# where a stale one would go unnoticed.
step "generated artifacts" ./tools/check-generated.sh
step "sql dialect map" ./tools/check-sql-map.sh
step "sql case data" ./tools/check-sql-cases.sh

for impl in $IMPLS; do
  step "sql translation ($impl)" impl_sql "$impl"
done

# Before the cases, because it is cheaper and because it asks a more basic
# question: can this host's own API build the map it ships? If it cannot, the
# map is data rather than code, and a host with no JSON reader has nowhere to
# put it. sql/MAP.md §4.5¼.
for impl in $IMPLS; do
  step "sql map replay ($impl)" impl_sqlreplay "$impl"
done

step "sql documented examples" ./tools/check-sql-docs.sh
step "sql mutations" ./tools/mutate-sql.sh
step "sql semantic oracle" ./tools/check-sql-oracle.sh
step "manifest versions" ./tools/check-version.sh
step "host API parity" ./tools/check-api.sh
step "documentation examples" ./tools/check-docs.sh
step "decimal vs python oracle" ./tools/check-decimal.sh "${DECIMAL_COUNT:-4000}"
step "end to end, every host API" ./tools/e2e.sh
step "differential fuzz" ./tools/fuzz.sh "${FUZZ_COUNT:-4000}" "${FUZZ_SEED:-20260813}"
step "differential fuzz, sql" ./tools/fuzz-sql.sh "${SQL_FUZZ_COUNT:-2000}" "${SQL_FUZZ_SEED:-20260905}"

echo
if [ "$status" -eq 0 ]; then
  echo "ALL GREEN — $IMPLS"
else
  echo "FAILURES ABOVE"
fi
exit "$status"
