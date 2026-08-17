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
# so a run with three of four hosts missing would otherwise pass having compared
# nothing with nothing.
if [ -n "$MISSING" ]; then
  echo "MISSING: $MISSING — not built, or the runtime is missing"
  echo "         build them (cd cpp && make) or set SEL_IMPLS to say so deliberately"
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

step "documentation examples" ./tools/check-docs.sh
step "decimal vs python oracle" ./tools/check-decimal.sh "${DECIMAL_COUNT:-4000}"
step "end to end, every host API" ./tools/e2e.sh
step "differential fuzz" ./tools/fuzz.sh "${FUZZ_COUNT:-4000}" "${FUZZ_SEED:-20260813}"

echo
if [ "$status" -eq 0 ]; then
  echo "ALL GREEN — $IMPLS"
else
  echo "FAILURES ABOVE"
fi
exit "$status"
