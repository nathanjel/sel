#!/usr/bin/env bash
# SQL API parity: the planner's contract through every host's own SQL binding,
# diffed -- tools/check-api.sh for the layer the bundles do not carry.
#
# sql/cases asserts what plan_hybrid CLASSIFIES and RENDERS for 89 shapes; this
# asks the other questions an application reads off a plan -- its dialect,
# continuation, source variable, tables, selected member -- through each host's
# accessors, and requires the same answers (SEL-0044). A host with no SQL layer
# prints nothing and is left out rather than failed: the JS bundles are built
# from js/src/sel.mjs, which does not import the layer.
set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pids=()
for impl in $(available_impls); do
  sel_slot impl_sqlapi "$impl" > "$WORK/$impl.txt" &
  pids+=($!)
done
sel_wait "${pids[@]}" || { echo "an SQL API probe exited non-zero" >&2; exit 1; }

IMPLS=""
for impl in $(available_impls); do
  [ -s "$WORK/$impl.txt" ] && IMPLS="$IMPLS $impl"
done
IMPLS="${IMPLS# }"
if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations with a SQL layer to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi
REF="${IMPLS%% *}"

status=0
for impl in $IMPLS; do
  [ "$impl" = "$REF" ] && continue
  if ! diff -u "$WORK/$REF.txt" "$WORK/$impl.txt" > "$WORK/$impl.diff"; then
    echo "SQL API MISMATCH between $REF and $impl (--- $REF, +++ $impl):"
    cat "$WORK/$impl.diff"
    status=1
  fi
done
if [ "$status" -eq 0 ]; then
  echo "$(wc -l < "$WORK/$REF.txt") SQL API probes, $IMPLS agree on every one"
fi
exit "$status"
