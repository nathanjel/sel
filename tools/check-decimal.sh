#!/usr/bin/env bash
# Checks every decimal core against Python's `decimal` as an independent oracle.
#
#   tools/check-decimal.sh [count] [seed]

set -euo pipefail
cd "$(dirname "$0")/.."
. tools/impls.sh

COUNT="${1:-4000}"
SEED="${2:-20260813}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

python3 tools/decimal-oracle.py "$COUNT" "$SEED" > "$WORK/oracle.txt"

# An empty oracle would give every implementation "0 cases, 0 mismatches" and a
# clean exit. Nothing to check is not the same as nothing wrong.
lines="$(wc -l < "$WORK/oracle.txt")"
if [ "$lines" -lt "$COUNT" ]; then
  echo "oracle produced $lines lines for $COUNT cases — generator failed" >&2
  exit 1
fi

status=0
for impl in $(available_impls); do
  impl_decimal "$impl" "$WORK/oracle.txt" || status=1
done
exit "$status"
