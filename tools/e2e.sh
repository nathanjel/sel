#!/usr/bin/env bash
# End-to-end check: the same rule set, driven through each host's own API against
# the same data, must produce identical results and identical dependencies().
#
# This is the test that exercises what the project is actually for — everything
# else tests the language, this tests the promise.

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

IMPLS="$(available_impls)"
# The first available implementation is the reference the others are diffed
# against; it is a reporting convenience only, since spec/ decides who is wrong.
REF="${IMPLS%% *}"

if [ "$(echo "$IMPLS" | wc -w)" -lt 2 ]; then
  echo "need at least two implementations to compare, have: ${IMPLS:-none}" >&2
  exit 1
fi

for impl in $IMPLS; do
  impl_e2e "$impl" > "$WORK/$impl.txt"
done

status=0
for impl in $IMPLS; do
  [ "$impl" = "$REF" ] && continue
  if ! diff -u "$WORK/$REF.txt" "$WORK/$impl.txt" > "$WORK/$impl.diff"; then
    echo "MISMATCH between $REF and $impl (--- $REF, +++ $impl):"
    cat "$WORK/$impl.diff"
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  cat "$WORK/$REF.txt"
  echo
  echo "$IMPLS agree on every scenario"
fi
exit "$status"
