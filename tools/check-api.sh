#!/usr/bin/env bash
# API parity: the same probes through every host's own binding, diffed.
#
# conformance/ checks the language; this checks the layer above it — that the
# host APIs offer the same operations and give the same answers. It exists
# because nothing did: every other layer drives the language through
# compile().run() and compares dump(), so four hosts could drift arbitrarily in
# API shape and stay green. They did, and a developer found it rather than the
# harness — the kind constants were reachable in PHP and unreachable in JS.

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS="$(available_impls)"
REF="${IMPLS%% *}"

if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi

for impl in $IMPLS; do
  impl_api "$impl" > "$WORK/$impl.txt"
  # An implementation that printed nothing must not compare equal to another
  # that printed nothing.
  if [ ! -s "$WORK/$impl.txt" ]; then
    echo "$impl produced no API report" >&2
    exit 1
  fi
done

status=0
for impl in $IMPLS; do
  [ "$impl" = "$REF" ] && continue
  if ! diff -u "$WORK/$REF.txt" "$WORK/$impl.txt" > "$WORK/$impl.diff"; then
    echo "API MISMATCH between $REF and $impl (--- $REF, +++ $impl):"
    cat "$WORK/$impl.diff"
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  echo "$(wc -l < "$WORK/$REF.txt") API probes, $IMPLS agree on every one"
fi
exit "$status"
